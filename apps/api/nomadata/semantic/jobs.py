"""Background semantic-model builds — the client submits a job and polls it.

A build does schema introspection, column profiling and batched AI enrichment,
which is far too slow for a single request. The work runs as an
in-process asyncio task; the client polls ``GET .../semantic/jobs/{id}`` for
progress and, when the job is ``done``, reloads the (already saved) draft. Jobs
are in-memory and ephemeral — fine for a single-process dev/app server.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    BusinessContext,
    ColumnProfile,
    DatabaseCatalog,
    DimensionKind,
    Entity,
    GenerationJob,
    JobStatus,
    MetricKind,
    MetricSuggestRequest,
    ProfileTarget,
    SemanticGraph,
)
from nomadata.core.registry import Registry
from nomadata.logging import describe_exception, get_logger
from nomadata.semantic.drafter import MetricSuggester
from nomadata.semantic.suggester import SemanticSuggester, is_temporal
from nomadata.storage.context_repo import BusinessContextRepository

log = get_logger()

_Coro = Coroutine[Any, Any, None]

#: Profiling is one query per column, so it is bounded: only columns that could
#: plausibly be a dimension, only on the tables in scope, with limited
#: concurrency so a build never hammers the user's production database.
_PROFILE_CONCURRENCY = 4
_MAX_PROFILED_COLUMNS = 400
_PROFILE_TIMEOUT_S = 15.0

#: How many fact-like entities a build proposes real metrics for. A count-only
#: model is not worth reviewing; but suggesting for every table would explode
#: cost, so the build spends its budget on the tables that look like facts.
_MAX_SUGGESTED_ENTITIES = 8
_SUGGEST_CONCURRENCY = 2
_SUGGEST_TIMEOUT_S = 60.0


class SemanticJobRunner:
    def __init__(
        self,
        registry: Registry,
        semantic: SemanticModel,
        contexts: BusinessContextRepository | None = None,
    ) -> None:
        self._registry = registry
        self._semantic = semantic
        self._contexts = contexts
        self._jobs: dict[str, GenerationJob] = {}
        self._active: dict[str, GenerationJob] = {}  # source_id -> running job
        self._tasks: set[asyncio.Task[None]] = set()

    def get(self, job_id: str) -> GenerationJob | None:
        return self._jobs.get(job_id)

    def active_for(self, source_id: str) -> GenerationJob | None:
        """The running job for a source, if any — lets the client resume watching
        after navigating away, and stops a second job for the same source."""
        return self._active.get(source_id)

    def start_generate(
        self,
        source_id: str,
        use_ai: bool,
        *,
        tables: list[str] | None = None,
        keep_edits: bool = True,
    ) -> GenerationJob:
        existing = self._active.get(source_id)
        if existing is not None:
            return existing  # one build per source — don't start a duplicate
        job = self._new(source_id, "generate")
        self._spawn(job, self._run_generate(job, source_id, use_ai, tables, keep_edits))
        return job

    def _new(self, source_id: str, kind: str) -> GenerationJob:
        job = GenerationJob(id=uuid.uuid4().hex, source_id=source_id, kind=kind)
        self._jobs[job.id] = job
        self._active[source_id] = job
        # Keep the map from growing without bound across a long-lived process.
        if len(self._jobs) > 200:
            for stale in list(self._jobs)[:100]:
                if self._jobs[stale].status != JobStatus.running:
                    del self._jobs[stale]
        return job

    def _spawn(self, job: GenerationJob, coro: _Coro) -> None:
        task = asyncio.create_task(self._guard(job, coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _guard(self, job: GenerationJob, coro: _Coro) -> None:
        try:
            await coro
            job.status = JobStatus.done
        except Exception as exc:  # noqa: BLE001 - surface any failure to the poller
            job.status = JobStatus.error
            job.error = describe_exception(exc)
            log.warning("semantic.job.failed", job=job.id, kind=job.kind, error=job.error)
        finally:
            if self._active.get(job.source_id) is job:
                del self._active[job.source_id]

    @staticmethod
    def _progress(job: GenerationJob) -> Callable[[int, int], None]:
        def report(done: int, total: int) -> None:
            job.done = done
            job.total = total

        return report

    @staticmethod
    def _batch_error(job: GenerationJob) -> Callable[[str], None]:
        """A naming batch that fails leaves its entities with heuristic names.
        The build is still usable, so it is not an error — but the client has to
        be able to say so instead of presenting a half-named model as done."""

        def report(reason: str) -> None:
            job.failed_batches += 1
            job.last_batch_error = reason

        return report

    async def _context(self, source_id: str) -> BusinessContext | None:
        if self._contexts is None:
            return None
        try:
            return await self._contexts.get(source_id)
        except Exception as exc:  # noqa: BLE001 - context is an enhancement, not a gate
            log.warning(
                "semantic.context.load_failed",
                source=source_id,
                error=describe_exception(exc),
            )
            return None

    async def _run_generate(
        self,
        job: GenerationJob,
        source_id: str,
        use_ai: bool,
        tables: list[str] | None,
        keep_edits: bool,
    ) -> None:
        source = self._registry.get_data_source(source_id)
        catalog = await source.inspect_schema()
        previous = await self._semantic.get_draft(source_id) if keep_edits else None
        scope = tables or (previous.scope_tables if previous else None) or None

        profiles = await profile_dimension_candidates(source, catalog, scope)
        provider = self._registry.active_provider()
        suggester = SemanticSuggester(provider)
        graph = suggester.heuristic(catalog, profiles=profiles, tables=scope, previous=previous)
        graph = graph.model_copy(update={"scope_tables": scope or []})

        if use_ai and provider is not None:
            context = await self._context(source_id)
            graph = await suggester.enrich_batched(
                graph,
                context=context,
                on_progress=self._progress(job),
                on_error=self._batch_error(job),
            )
            # A model of nothing but row counts is not worth reviewing. Propose
            # real metrics for the tables that look like facts — as suggestions
            # the user keeps or deletes, never as published fact.
            graph = await self._suggest_metrics(job, graph, provider, context)
        await self._save(graph, source_id, previous)

    async def _suggest_metrics(
        self,
        job: GenerationJob,
        graph: SemanticGraph,
        provider: object,
        context: BusinessContext | None,
    ) -> SemanticGraph:
        facts = _fact_entities(graph)
        if not facts:
            return graph

        base_total = job.total  # enrichment's bar is the one shown to the user
        suggester = MetricSuggester(provider)  # type: ignore[arg-type]
        semaphore = asyncio.Semaphore(_SUGGEST_CONCURRENCY)
        extra: dict[str, list] = {}

        async def run(entity: Entity) -> None:
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        suggester.suggest(
                            MetricSuggestRequest(entity_key=entity.key),
                            graph,
                            context=context,
                        ),
                        timeout=_SUGGEST_TIMEOUT_S,
                    )
                    extra[entity.key] = result.metrics
                except Exception as exc:  # noqa: BLE001 - a table without metrics is fine
                    self._batch_error(job)(describe_exception(exc))

        await asyncio.gather(*[run(e) for e in facts])
        job.total = base_total

        # Dedup once more across the batch: two fact tables can each propose
        # "revenue", and only the recipe tells them apart.
        seen = {_metric_recipe(m) for m in graph.metrics}
        added: list = []
        for metrics in extra.values():
            for metric in metrics:
                recipe = _metric_recipe(metric)
                if recipe not in seen:
                    seen.add(recipe)
                    added.append(metric)
        if not added:
            return graph
        return graph.model_copy(update={"metrics": [*graph.metrics, *added]})

    async def _save(
        self, graph: SemanticGraph, source_id: str, previous: SemanticGraph | None
    ) -> None:
        # A build takes minutes; the user may have saved an edit meanwhile. The
        # job is the one writer that must not fail on a conflict — it merged the
        # previous draft already — so it writes unconditionally.
        await self._semantic.save_draft(graph.model_copy(update={"source_id": source_id}))


def _fact_entities(graph: SemanticGraph) -> list[Entity]:
    """Entities that look like they record events worth measuring.

    A fact table has a date (when the event happened) and a non-key number (the
    amount). Lookup and junction tables have neither, so they are not worth an
    AI call — and the suggester would return nothing for them anyway. Ranked by
    how much a table carries, capped so a build stays affordable.
    """
    scored: list[tuple[int, Entity]] = []
    for entity in graph.entities:
        if entity.hidden:
            continue
        visible = [d for d in entity.dimensions if not d.hidden]
        has_time = any(d.kind == DimensionKind.time for d in visible)
        numbers = [
            d for d in visible if d.kind == DimensionKind.number and not d.column.endswith("_id")
        ]
        if not (has_time and numbers):
            continue
        # Skip a table the user already gave real (non-count) metrics.
        has_real = any(
            m.entity_key == entity.key
            and m.kind == MetricKind.base
            and (m.aggregation is None or str(m.aggregation) != "count")
            for m in graph.metrics
        )
        if has_real:
            continue
        scored.append((len(numbers), entity))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entity for _, entity in scored[:_MAX_SUGGESTED_ENTITIES]]


def _metric_recipe(metric: object) -> str:
    """Local copy of the drafter's recipe key, to dedup across suggestion
    batches without importing a private helper."""
    kind = getattr(metric, "kind", None)
    if str(kind) == "MetricKind.derived" or getattr(kind, "value", "") == "derived":
        return f"={(getattr(metric, 'expression', '') or '').strip()}"
    agg = getattr(metric, "aggregation", None)
    column = getattr(metric, "column", None) or "*"
    filters = sorted(f"{f.field}{f.operator}{f.value}" for f in getattr(metric, "filters", []))
    return f"{agg}({column})[{','.join(filters)}]"


def _dimension_candidates(
    catalog: DatabaseCatalog, tables: list[str] | None
) -> list[ProfileTarget]:
    """Columns worth profiling: anything that could be a dimension.

    Numeric non-key columns are included too — ``status_id`` is a dimension and
    ``so_tien`` is a measure, and only the distinct count tells them apart.
    Temporal columns are skipped: they are dimensions by definition.
    """
    wanted = set(tables) if tables else None
    targets: list[ProfileTarget] = []
    for table in catalog.tables:
        if wanted is not None and table.name not in wanted:
            continue
        primary = set(table.primary_key) | {c.name for c in table.columns if c.is_primary_key}
        for column in table.columns:
            if column.name in primary or is_temporal(column.data_type):
                continue
            targets.append(
                ProfileTarget(
                    table=table.name,
                    column=column.name,
                    schema_name=table.schema_name,
                )
            )
    return targets


async def profile_dimension_candidates(
    source: DataSource,
    catalog: DatabaseCatalog,
    tables: list[str] | None,
) -> dict[tuple[str, str], ColumnProfile]:
    """Profile candidate dimension columns, best-effort.

    A column's distinct count is what separates a useful dimension (``status``,
    5 values) from a useless one (``note``, one value per row) — a distinction
    the data type cannot make. Failures are skipped: profiling improves the
    draft, it must never block one.
    """
    targets = _dimension_candidates(catalog, tables)
    if len(targets) > _MAX_PROFILED_COLUMNS:
        # Without a chosen scope this could be thousands of queries. Profile what
        # fits and let the rest fall back to type-only classification.
        log.info(
            "semantic.profile.truncated",
            total=len(targets),
            profiled=_MAX_PROFILED_COLUMNS,
        )
        targets = targets[:_MAX_PROFILED_COLUMNS]

    profiles: dict[tuple[str, str], ColumnProfile] = {}
    semaphore = asyncio.Semaphore(_PROFILE_CONCURRENCY)

    async def run(target: ProfileTarget) -> None:
        async with semaphore:
            try:
                profile = await asyncio.wait_for(source.profile(target), timeout=_PROFILE_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - a column we cannot profile is fine
                return
            profiles[(target.table, target.column)] = profile

    await asyncio.gather(*[run(t) for t in targets])
    return profiles
