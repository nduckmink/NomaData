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
from itertools import zip_longest
from typing import Any

from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    BusinessContext,
    ColumnProfile,
    DatabaseCatalog,
    Entity,
    GenerationJob,
    JobStatus,
    MetricDefinition,
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
_PROFILE_TIMEOUT_S = 15.0

#: No global ceiling on purpose. A build is a deliberate, one-off act with a
#: progress bar in front of it, and the thing it produces is used until somebody
#: rebuilds — so stopping early trades a few minutes once against a model that
#: knows nothing about its own columns for as long as it lives. The per-column
#: timeout above is the guard that belongs here: one pathological column is
#: skipped, the other thousand are not. A database too large for this is one
#: where the user narrows the table scope, which the build screen already
#: offers — a choice made with the numbers in front of them, not one made
#: silently on their behalf.

#: Which entities get metric proposals is decided by the naming pass, not by a
#: number here — see ``_measurable_entities``. A count-only model is not worth
#: reviewing, and the tables worth measuring are the ones a person would name.
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

        profiles = await profile_dimension_candidates(source, catalog, scope, job)
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
            # Ratios come second: the pass above is what gives an entity the
            # two numbers a ratio needs.
            graph = await self._suggest_derived(job, graph, provider, context)
        await self._save(graph, source_id, previous)

    async def _suggest_metrics(
        self,
        job: GenerationJob,
        graph: SemanticGraph,
        provider: object,
        context: BusinessContext | None,
    ) -> SemanticGraph:
        facts = _measurable_entities(graph)
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

    async def _suggest_derived(
        self,
        job: GenerationJob,
        graph: SemanticGraph,
        provider: object,
        context: BusinessContext | None,
    ) -> SemanticGraph:
        """Ratios over the base metrics an entity has just been given.

        A total says how much happened; a ratio says whether it went well, and
        that second kind is what a business steers by. It cannot be proposed in
        the pass above because at that point a table has only a row count, and a
        ratio needs two numbers.

        Only entities with two base metrics or more, so no call is spent on a
        table that has nothing to combine.
        """
        candidates = [
            entity
            for entity in graph.entities
            if not entity.hidden
            and len(
                [
                    m
                    for m in graph.metrics
                    if m.entity_key == entity.key
                    and m.kind == MetricKind.base
                    and not _is_plain_count(m)
                ]
            )
            >= 2
        ]
        if not candidates:
            return graph

        base_total = job.total
        suggester = MetricSuggester(provider)  # type: ignore[arg-type]
        semaphore = asyncio.Semaphore(_SUGGEST_CONCURRENCY)
        extra: dict[str, list] = {}

        async def run(entity: Entity) -> None:
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        suggester.suggest_derived(entity.key, graph, context=context),
                        timeout=_SUGGEST_TIMEOUT_S,
                    )
                    extra[entity.key] = result.metrics
                except Exception as exc:  # noqa: BLE001 - a table without ratios is fine
                    self._batch_error(job)(describe_exception(exc))

        await asyncio.gather(*[run(e) for e in candidates])
        job.total = base_total

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


def _measurable_entities(graph: SemanticGraph) -> list[Entity]:
    """Entities worth asking an AI to design metrics for.

    The old rule looked for a date and a non-key number and took the top eight.
    Structure cannot tell a table anyone measures from one nobody does — on this
    source it called 95 of 122 tables facts, including permission tables and
    audit logs — and the cap of eight then meant the judgement was made by
    whichever of those happened to have the most numeric columns.

    The naming pass has already answered the question properly, table by table,
    at no extra cost, and it recorded its answer by leaving the row count in
    place or removing it. So the tables that still carry a count are the tables
    somebody measures, and that is the scope. No cap: the scope came from a
    judgement about the business rather than from a number we chose, and cutting
    it again here would put the arbitrary limit straight back.
    """
    measurable: list[Entity] = []
    for entity in graph.entities:
        if entity.hidden:
            continue
        mine = [m for m in graph.metrics if m.entity_key == entity.key]
        # A table the user has already given real metrics needs no proposals.
        if any(
            m.kind == MetricKind.base and (m.aggregation is None or str(m.aggregation) != "count")
            for m in mine
        ):
            continue
        if any(_is_plain_count(m) for m in mine):
            measurable.append(entity)
    return measurable


def _is_plain_count(metric: MetricDefinition) -> bool:
    return (
        metric.kind == MetricKind.base
        and metric.aggregation is not None
        and str(metric.aggregation).endswith("count")
        and not metric.filters
        and not metric.column
    )


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
    job: GenerationJob | None = None,
) -> dict[tuple[str, str], ColumnProfile]:
    """Profile candidate dimension columns, best-effort.

    A column's distinct count is what separates a useful dimension (``status``,
    5 values) from a useless one (``note``, one value per row) — a distinction
    the data type cannot make. Failures are skipped: profiling improves the
    draft, it must never block one.
    """
    targets = _interleave_by_table(_dimension_candidates(catalog, tables))
    profiles: dict[tuple[str, str], ColumnProfile] = {}
    semaphore = asyncio.Semaphore(_PROFILE_CONCURRENCY)
    failed = 0
    if job is not None:
        job.profile_total = len(targets)

    async def run(target: ProfileTarget) -> None:
        nonlocal failed
        async with semaphore:
            try:
                profile = await asyncio.wait_for(source.profile(target), timeout=_PROFILE_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - a column we cannot profile is fine
                failed += 1
            else:
                profiles[(target.table, target.column)] = profile
            if job is not None:
                # Counted as it goes, so the screen can say what it is doing
                # instead of holding a bar still for several minutes.
                job.profiled_columns = len(profiles)
                job.unprofiled_columns = failed

    await asyncio.gather(*[run(t) for t in targets])
    if failed:
        log.warning("semantic.profile.incomplete", profiled=len(profiles), failed=failed)
    return profiles


def _interleave_by_table(targets: list[ProfileTarget]) -> list[ProfileTarget]:
    """One column from each table, then the next, and so on.

    The catalogue comes ordered by table, so taking the list as it stands spends
    everything on whatever sorts first: on the source this was found with, the
    budget went entirely to ``category_*`` lookup tables and left ``transactions``
    and ``enterprises`` — the two the metrics live on — with nothing at all.

    Round-robin means running out of time costs every table its rarest columns
    rather than costing some tables everything.
    """
    by_table: dict[str, list[ProfileTarget]] = {}
    for target in targets:
        by_table.setdefault(target.table, []).append(target)

    ordered: list[ProfileTarget] = []
    for row in zip_longest(*by_table.values()):
        ordered.extend(t for t in row if t is not None)
    return ordered
