"""Draft a SemanticGraph from a DatabaseCatalog.

Two layers, both producing the same artifact:

- **Heuristic baseline** (always runs, no key needed): tables with a primary key
  become entities, their columns are classified into dimensions using the real
  column type plus profiling (a column is a good dimension when it has *few
  distinct values*, not when it happens to be text), foreign keys become
  relationships, and each entity gets one lean base metric — a ``<Entity>
  Count``. SUM/AVG metrics come from the user or from an AI proposal, because
  auto-summing every numeric column produces mostly noise: IDs, codes, flags.
- **AI enrichment** (when an ``AIProvider`` is available): entities and their
  metrics are sent together, in small batches, and the model returns business
  names and descriptions. The AI *proposes* — the output is a draft a human
  still reviews and publishes; on any failure we keep the heuristic text.

Everything is addressed by ``Entity.key`` / ``MetricDefinition.id``, never by
display name: renaming is what the AI does most, and names must therefore be
free to change without orphaning anything.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.models import (
    Aggregation,
    BusinessContext,
    ColumnInfo,
    ColumnProfile,
    DatabaseCatalog,
    Dimension,
    DimensionKind,
    EnrichmentHints,
    Entity,
    EntityHint,
    Message,
    MetricDefinition,
    MetricHint,
    MetricKind,
    Origin,
    Provenance,
    Relationship,
    Role,
    SemanticGraph,
    TableInfo,
)
from nomadata.logging import describe_exception, get_logger
from nomadata.semantic.prompt import context_rules

log = get_logger()

# Server-side enrichment batching: small batches keep each AI call fast and
# valid; low concurrency avoids piling up requests on a rate-limited provider.
_ENRICH_BATCH = 8
_ENRICH_CONCURRENCY = 2
# Must exceed the provider's own HTTP timeout, and cover the one retry it
# makes on invalid JSON. At 45s this fired *before* the provider could give
# up, so every slow reply surfaced as a bare TimeoutError with no message.
_ENRICH_BATCH_TIMEOUT_S = 150.0

# Dimensions whose values are almost all distinct (free text, notes, raw blobs)
# are useless to group by — keep them in the model but hidden by default.
_MAX_DIMENSION_DISTINCT_RATIO = 0.5
_MAX_SAMPLE_VALUES = 12

_NUMERIC_TYPES = (
    "int",
    "integer",
    "bigint",
    "smallint",
    "tinyint",
    "mediumint",
    "decimal",
    "numeric",
    "float",
    "double",
    "real",
    "money",
    "number",
)
_TEMPORAL_TYPES = ("date", "time", "timestamp", "datetime", "year", "datetimeoffset")
_BOOLEAN_TYPES = ("bool", "bit")

# Column-name shapes that are plumbing rather than business meaning. Hidden by
# default so a 40-column table doesn't drown the reviewer; still editable.
_TECHNICAL_COLUMN = re.compile(
    r"(^|_)(guid|uuid|hash|token|password|salt|version|rowversion|"
    r"created_by|updated_by|modified_by|is_deleted|deleted_at)($|_)",
    re.IGNORECASE,
)


def entity_key(schema_name: str, table: str) -> str:
    """The immutable identity of an entity. Schema-qualified so two tables with
    the same name in different schemas never collide."""
    return f"{schema_name or 'public'}.{table}"


def humanize(identifier: str) -> str:
    """``order_items`` / ``OrderItems`` → ``Order Items`` (a readable default)."""
    spaced = identifier.replace("_", " ").replace("-", " ").strip()
    if not spaced:
        return identifier
    return " ".join(word.capitalize() for word in spaced.split())


def is_numeric(data_type: str) -> bool:
    return any(token in data_type.lower() for token in _NUMERIC_TYPES)


def is_temporal(data_type: str) -> bool:
    return any(token in data_type.lower() for token in _TEMPORAL_TYPES)


def is_boolean(data_type: str) -> bool:
    dt = data_type.lower()
    return any(token in dt for token in _BOOLEAN_TYPES) and "bigint" not in dt


def dimension_kind(data_type: str) -> DimensionKind:
    """The real type, read from the catalog. Guessing this from the column name
    downstream (``runtime`` → time, ``discount_count`` → number) produced Cube
    models that fail at query time."""
    if is_temporal(data_type):
        return DimensionKind.time
    if is_boolean(data_type):
        return DimensionKind.boolean
    if is_numeric(data_type):
        return DimensionKind.number
    return DimensionKind.string


def _pk_columns(table: TableInfo) -> list[str]:
    if table.primary_key:
        return table.primary_key
    return [c.name for c in table.columns if c.is_primary_key]


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _writable(prov: Provenance) -> bool:
    """May AI rewrite this object's business text? Only when a human hasn't
    claimed it — an explicit flag, never inferred from the string's shape."""
    return prov.origin != Origin.user and not prov.locked


def _is_technical(column_name: str) -> bool:
    """Plumbing rather than business meaning (audit columns, hashes, tokens)."""
    return bool(_TECHNICAL_COLUMN.search(column_name))


def _is_too_unique(profile: ColumnProfile | None, kind: DimensionKind) -> bool:
    """Almost every row has its own value — grouping by it yields one group per
    row. Dates and booleans are exempt: they are dimensions by nature, however
    many distinct values a date column happens to hold."""
    if profile is None or profile.is_categorical is not False:
        return False
    return kind not in (DimensionKind.time, DimensionKind.boolean)


def _dimension_for(
    column: ColumnInfo,
    *,
    is_foreign_key: bool,
    profile: ColumnProfile | None,
) -> Dimension:
    kind = dimension_kind(column.data_type)
    distinct = profile.distinct_count if profile else None
    samples = list(profile.sample_values[:_MAX_SAMPLE_VALUES]) if profile else []

    # Hide what is useless (or unsafe) to group by. A foreign key is always
    # worth keeping: it is how the user slices by a related entity.
    hidden = not is_foreign_key and (_is_technical(column.name) or _is_too_unique(profile, kind))

    # Samples are only meaningful (and only small) for low-cardinality columns.
    if profile is not None and profile.is_categorical is not True:
        samples = []

    return Dimension(
        name=humanize(column.name),
        column=column.name,
        kind=kind,
        data_type=column.data_type,
        hidden=hidden,
        distinct_count=distinct,
        sample_values=samples,
        description=None,
    )


class SemanticSuggester:
    def __init__(self, provider: AIProvider | None = None) -> None:
        self._provider = provider

    # ------------------------------------------------------------------
    # Heuristic baseline
    # ------------------------------------------------------------------

    def heuristic(
        self,
        catalog: DatabaseCatalog,
        *,
        profiles: dict[tuple[str, str], ColumnProfile] | None = None,
        tables: list[str] | None = None,
        previous: SemanticGraph | None = None,
    ) -> SemanticGraph:
        """Build a structural draft from the schema.

        ``profiles`` maps ``(table, column)`` to a profile and sharpens the
        dimension classification. ``tables`` limits the model to a chosen scope.
        ``previous`` lets a rebuild keep everything a human already edited —
        regenerating must not silently discard reviewed work.
        """
        profiles = profiles or {}
        wanted = set(tables) if tables else None

        kept: list[Entity] = []
        relationships: list[Relationship] = []
        metrics: list[MetricDefinition] = []
        skipped: list[dict[str, str]] = []

        selected = [t for t in catalog.tables if wanted is None or t.name in wanted]
        entity_keys = {entity_key(t.schema_name, t.name) for t in selected if _pk_columns(t)}
        fk_columns_by_table = {t.name: {fk.column for fk in t.foreign_keys} for t in selected}

        for table in selected:
            pk = _pk_columns(table)
            if not pk:
                skipped.append({"table": table.name, "reason": "no_primary_key"})
                continue
            key = entity_key(table.schema_name, table.name)
            pk_set = set(pk)
            fk_columns = fk_columns_by_table.get(table.name, set())

            dimensions = [
                _dimension_for(
                    column,
                    is_foreign_key=column.name in fk_columns,
                    profile=profiles.get((table.name, column.name)),
                )
                for column in table.columns
                if column.name not in pk_set
            ]
            entity = Entity(
                key=key,
                name=humanize(table.name),
                table=table.name,
                schema_name=table.schema_name,
                primary_key=pk[0],
                dimensions=dimensions,
                description=f"Business entity backed by table {table.name}.",
            )
            kept.append(entity)

            # One lean, always-correct base metric per entity: how many exist.
            # SUM/AVG over specific columns come from the user or an AI proposal
            # (most numeric columns are IDs/codes/flags, so auto-summing is noise).
            metrics.append(
                MetricDefinition(
                    name=f"{entity.name} Count",
                    description=f"Number of {entity.name} records.",
                    kind=MetricKind.base,
                    entity_key=key,
                    aggregation=Aggregation.count,
                )
            )

            for fk in table.foreign_keys:
                target = next((t for t in selected if t.name == fk.references_table), None)
                if target is None:
                    continue
                target_key = entity_key(target.schema_name, target.name)
                if target_key in entity_keys:
                    relationships.append(
                        Relationship(
                            from_entity_key=key,
                            to_entity_key=target_key,
                            from_column=fk.column,
                            to_column=fk.references_column,
                            kind="many_to_one",
                        )
                    )

        graph = SemanticGraph(
            source_id=catalog.source_id,
            entities=kept,
            metrics=metrics,
            relationships=relationships,
            provenance="heuristic",
            skipped_tables=skipped,
        )
        if previous is not None:
            graph = merge_preserving_edits(graph, previous)
        return graph

    # ------------------------------------------------------------------
    # AI enrichment
    # ------------------------------------------------------------------

    async def enrich_hints(
        self,
        graph: SemanticGraph,
        *,
        context: BusinessContext | None = None,
    ) -> EnrichmentHints:
        """Return compact business text (name + description/definition) for each
        entity and metric in the given slice, matched back by key/id. The reply
        echoes no structure, so it stays small and fast. Requires a provider."""
        if self._provider is None:
            raise RuntimeError("No AI provider configured for enrichment.")

        metrics_by_entity: dict[str | None, list[MetricDefinition]] = {}
        for metric in graph.metrics:
            metrics_by_entity.setdefault(metric.entity_key, []).append(metric)

        payload = []
        for entity in graph.entities:
            payload.append(
                {
                    "key": entity.key,
                    "table": entity.table,
                    # Visible columns only, and the samples that make a cryptic
                    # column ("sts" → ['A','P','C']) understandable.
                    "columns": [
                        {
                            "column": d.column,
                            "type": str(d.kind),
                            "samples": d.sample_values[:4] or None,
                        }
                        for d in entity.dimensions
                        if not d.hidden
                    ][:20],
                    "metrics": [
                        {
                            "id": m.id,
                            "aggregation": str(m.aggregation) if m.aggregation else None,
                            "column": m.column,
                            "expression": m.expression,
                        }
                        for m in metrics_by_entity.get(entity.key, [])
                    ],
                }
            )

        messages = [
            Message(
                role=Role.system,
                content=(
                    "You name and describe objects in a business intelligence "
                    "semantic model. For each entity (a database table) write a "
                    "concise business Name and a one-sentence Description. For "
                    "each metric (an aggregation over a column of that entity) "
                    "write a business Name and a one-sentence Definition of what "
                    "it measures. Return ONLY JSON. Echo every entity `key` and "
                    "metric `id` back EXACTLY as given so they can be matched. "
                    "Do not invent items, tables or columns.\n"
                    "Also set `measurable` on each entity: true when a person "
                    "running this business would ask how many of these there "
                    "are or what they add up to — orders, payments, customers, "
                    "advances. False for the rest: lookup and category tables, "
                    "join tables, permission and configuration tables, audit "
                    "logs. Most tables in a large database are false. Be strict: "
                    "a metric nobody asks for still has to be read past every "
                    "time somebody looks for one that matters." + context_rules(context)
                ),
            ),
            Message(
                role=Role.user,
                content=(
                    f"Entities with their columns and metrics:\n"
                    f"{_json(payload)}\n\n"
                    'Return {"entities":[{"key","name","description","measurable"}],'
                    '"metrics":[{"id","name","definition"}]}.'
                ),
            ),
        ]
        return await self._provider.generate_structured(messages, EnrichmentHints)

    async def enrich_batched(
        self,
        graph: SemanticGraph,
        *,
        context: BusinessContext | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> SemanticGraph:
        """AI-improve names/descriptions across the whole graph, in small
        bounded-concurrency batches, then merge — writing only where a human has
        not. Batches carry an entity together with *its* metrics, so the model
        names a metric knowing what it belongs to. No-op without a provider."""
        if self._provider is None:
            return graph

        metrics_by_entity: dict[str | None, list[MetricDefinition]] = {}
        for metric in graph.metrics:
            metrics_by_entity.setdefault(metric.entity_key, []).append(metric)

        targets = [e for e in graph.entities if _writable(e.provenance)]
        batches = _chunk(targets, _ENRICH_BATCH)
        total = len(batches)
        done = 0
        ent_hints: dict[str, EntityHint] = {}
        met_hints: dict[str, MetricHint] = {}
        sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

        def _tick() -> None:
            nonlocal done
            done += 1
            if on_progress is not None:
                on_progress(done, total)

        async def _run(entities: list[Entity]) -> None:
            async with sem:
                try:
                    slice_metrics = [
                        m
                        for e in entities
                        for m in metrics_by_entity.get(e.key, [])
                        # A "<Entity> Count" needs no AI naming — it is already
                        # right, and asking doubled the cost of every build.
                        if _writable(m.provenance) and not _is_plain_count(m)
                    ]
                    hints = await asyncio.wait_for(
                        self.enrich_hints(
                            SemanticGraph(
                                source_id=graph.source_id,
                                entities=entities,
                                metrics=slice_metrics,
                            ),
                            context=context,
                        ),
                        timeout=_ENRICH_BATCH_TIMEOUT_S,
                    )
                    for eh in hints.entities:
                        ent_hints[eh.key] = eh
                    for mh in hints.metrics:
                        met_hints[mh.id] = mh
                except Exception as exc:  # noqa: BLE001 - a failed batch is skipped
                    # A skipped batch leaves those entities with their heuristic
                    # names, which looks like success — so it is counted and
                    # surfaced, not just logged.
                    reason = describe_exception(exc)
                    log.warning(
                        "semantic.enrich.batch_failed",
                        error=reason,
                        entities=[e.key for e in entities],
                    )
                    if on_error is not None:
                        on_error(reason)
                finally:
                    _tick()

        await asyncio.gather(*[_run(batch) for batch in batches])

        entities: list[Entity] = []
        for entity in graph.entities:
            hint = ent_hints.get(entity.key)
            if hint is not None and _writable(entity.provenance):
                update: dict[str, Any] = {"provenance": Provenance(origin=Origin.ai)}
                if hint.name.strip():
                    update["name"] = hint.name.strip()
                if hint.description.strip():
                    update["description"] = hint.description.strip()
                entity = entity.model_copy(update=update)
            entities.append(entity)

        # Renaming an entity must never dangle its metrics: they point at the
        # immutable key, and a "<Entity> Count" default label is refreshed to
        # follow the new name.
        renamed = {e.key: e.name for e in entities}
        previous_names = {e.key: e.name for e in graph.entities}

        # Tables nobody measures keep their columns — they are still worth
        # slicing by — but lose the row count the heuristic gave every table on
        # principle. 122 of those buried the 16 metrics anyone had designed, and
        # every one of them was a name the agent had to read past. A table the
        # model never judged keeps its count: silence is not a no.
        unmeasured = {hint.key for hint in ent_hints.values() if not hint.measurable}

        metrics: list[MetricDefinition] = []
        for metric in graph.metrics:
            if (
                _is_plain_count(metric)
                and metric.entity_key in unmeasured
                and _writable(metric.provenance)
            ):
                continue
            metric_hint = met_hints.get(metric.id)
            if _writable(metric.provenance):
                update = {}
                if metric_hint is not None:
                    if metric_hint.name.strip():
                        update["name"] = metric_hint.name.strip()
                    if metric_hint.definition.strip():
                        update["description"] = metric_hint.definition.strip()
                    update["provenance"] = Provenance(origin=Origin.ai)
                elif _is_plain_count(metric) and metric.entity_key in renamed:
                    old = previous_names.get(metric.entity_key, "")
                    if metric.name == f"{old} Count":
                        new = renamed[metric.entity_key]
                        update["name"] = f"{new} Count"
                        update["description"] = f"Number of {new} records."
                if update:
                    metric = metric.model_copy(update=update)
            metrics.append(metric)

        return graph.model_copy(
            update={"entities": entities, "metrics": metrics, "provenance": "ai"}
        )


def _is_plain_count(metric: MetricDefinition) -> bool:
    """An unfiltered row count — the one metric the heuristic always gets right."""
    return (
        metric.kind == MetricKind.base
        and metric.aggregation == Aggregation.count
        and not metric.filters
        and not metric.column
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _keep_text(fresh: Any, prior: Any, provenance: Provenance) -> Any:
    """Prior text wins when a human owns it; otherwise take the fresh value."""
    return fresh if _writable(provenance) else prior


def merge_preserving_edits(fresh: SemanticGraph, previous: SemanticGraph) -> SemanticGraph:
    """Fold a freshly introspected graph onto an existing one.

    Rebuilding from the schema must not throw away reviewed work: anything a
    human touched (``origin == user`` or ``locked``) wins, user-authored metrics
    survive, and only genuinely new tables/columns are added. Tables that
    disappeared from the database drop out, because they really are gone.
    """
    old_entities = {e.key: e for e in previous.entities}
    entities: list[Entity] = []
    for entity in fresh.entities:
        old = old_entities.get(entity.key)
        if old is None:
            entities.append(entity)
            continue

        old_dims = {d.column: d for d in old.dimensions}
        dimensions = []
        for dim in entity.dimensions:
            prior = old_dims.get(dim.column)
            if prior is not None:
                # Fresh type and profiling always win (they were just read from
                # the database); naming and the show/hide decision stay human.
                dim = dim.model_copy(
                    update={
                        "name": _keep_text(dim.name, prior.name, prior.provenance),
                        "description": _keep_text(
                            dim.description, prior.description, prior.provenance
                        ),
                        "hidden": prior.hidden,
                        "provenance": prior.provenance,
                    }
                )
            dimensions.append(dim)

        entities.append(
            entity.model_copy(
                update={
                    "name": _keep_text(entity.name, old.name, old.provenance),
                    "description": _keep_text(entity.description, old.description, old.provenance),
                    "hidden": old.hidden,
                    "provenance": old.provenance,
                    "dimensions": dimensions,
                }
            )
        )

    live_keys = {e.key for e in entities}
    # Every previous metric the user owns is kept; the fresh heuristic Count is
    # dropped when an equivalent one already exists.
    metrics: list[MetricDefinition] = [
        m for m in previous.metrics if m.entity_key in live_keys or m.kind == MetricKind.derived
    ]
    existing_counts = {m.entity_key for m in metrics if _is_plain_count(m)}
    for metric in fresh.metrics:
        if _is_plain_count(metric) and metric.entity_key in existing_counts:
            continue
        metrics.append(metric)

    return fresh.model_copy(
        update={
            "entities": entities,
            "metrics": metrics,
            "provenance": previous.provenance,
        }
    )
