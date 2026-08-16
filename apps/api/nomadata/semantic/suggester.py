"""Draft a SemanticGraph from a DatabaseCatalog.

Two layers, both producing the same artifact:

- **Heuristic baseline** (always runs, no key needed): tables with a primary key
  become entities, categorical/date columns become dimensions, foreign keys
  become relationships, and each entity gets one lean base metric — a
  ``<Entity> Count``. SUM/AVG metrics are added by the user (auto-summing every
  numeric column produces mostly noise: IDs, codes, flags).
- **AI enrichment** (when an ``AIProvider`` is available): the baseline is handed
  to the model, which returns business names, descriptions and richer metric
  definitions. The AI *proposes* — the output is a draft a human still reviews
  and publishes; on any failure we fall back to the heuristic graph.
"""

from __future__ import annotations

import json

from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.models import (
    Aggregation,
    ColumnInfo,
    DatabaseCatalog,
    Dimension,
    EnrichmentHints,
    Entity,
    Message,
    MetricDefinition,
    MetricKind,
    Relationship,
    Role,
    SemanticGraph,
    TableInfo,
)
from nomadata.logging import get_logger

log = get_logger()

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


def _humanize(identifier: str) -> str:
    """``order_items`` / ``OrderItems`` → ``Order Items`` (a readable default)."""
    spaced = identifier.replace("_", " ").replace("-", " ").strip()
    if not spaced:
        return identifier
    return " ".join(word.capitalize() for word in spaced.split())


def _is_numeric(data_type: str) -> bool:
    dt = data_type.lower()
    return any(token in dt for token in _NUMERIC_TYPES)


def _is_temporal(data_type: str) -> bool:
    dt = data_type.lower()
    return any(token in dt for token in _TEMPORAL_TYPES)


def _pk_columns(table: TableInfo) -> list[str]:
    if table.primary_key:
        return table.primary_key
    return [c.name for c in table.columns if c.is_primary_key]


def _dimension_for(column: ColumnInfo) -> Dimension:
    kind = "time" if _is_temporal(column.data_type) else "attribute"
    return Dimension(
        name=_humanize(column.name),
        column=column.name,
        description=f"{kind.capitalize()} dimension on {column.name}.",
    )


def metric_key(m: MetricDefinition) -> str:
    """Stable identifier for a metric, used to match AI enrichment back and to
    dedupe. Must match the frontend's computation."""
    if m.kind == MetricKind.derived:
        return f"={m.expression or ''}"
    return f"{m.aggregation}({m.entity}.{m.column or '*'})"


class SemanticSuggester:
    def __init__(self, provider: AIProvider | None = None) -> None:
        self._provider = provider

    async def suggest(self, catalog: DatabaseCatalog, *, use_ai: bool = True) -> SemanticGraph:
        """Return a draft SemanticGraph. Uses AI enrichment when a provider is
        available and ``use_ai`` is set; otherwise the heuristic baseline."""
        heuristic = self.heuristic(catalog)
        if use_ai and self._provider is not None:
            try:
                enriched = await self._enrich_graph(heuristic)
                return enriched.model_copy(
                    update={"source_id": catalog.source_id, "provenance": "ai"}
                )
            except Exception as exc:  # noqa: BLE001 - AI is best-effort; degrade to heuristic
                log.warning("semantic.suggest.ai_failed", error=str(exc))
        return heuristic

    async def enrich_hints(self, graph: SemanticGraph) -> EnrichmentHints:
        """Return compact business text (name + description/definition) for each
        entity and metric — matched back by table/formula. The response echoes
        no structure, so it stays small and fast even in batches. Requires a
        provider; callers should send a manageable slice of the graph."""
        if self._provider is None:
            raise RuntimeError("No AI provider configured for enrichment.")

        entities_in = [
            {"table": e.table, "columns": [d.column for d in e.dimensions][:12]}
            for e in graph.entities
        ]
        metrics_in = [
            {
                "key": metric_key(m),
                "entity": m.entity,
                "aggregation": str(m.aggregation) if m.aggregation else None,
                "column": m.column,
                "expression": m.expression,
            }
            for m in graph.metrics
        ]
        messages = [
            Message(
                role=Role.system,
                content=(
                    "You name and describe objects in a business intelligence "
                    "semantic model. For each entity (a database table) write a "
                    "concise business Name and a one-sentence Description. For "
                    "each metric (an aggregation over a column, or an expression "
                    "over other metrics) write a business Name and a one-sentence "
                    "Definition of what it measures. Return ONLY JSON. Echo each "
                    "entity `table` and metric `key` back EXACTLY as given so it "
                    "can be matched. Do not invent items."
                ),
            ),
            Message(
                role=Role.user,
                content=(
                    f"Entities: {json.dumps(entities_in, ensure_ascii=False)}\n"
                    f"Metrics: {json.dumps(metrics_in, ensure_ascii=False)}\n\n"
                    'Return {"entities":[{"table","name","description"}],'
                    '"metrics":[{"key","name","definition"}]}.'
                ),
            ),
        ]
        return await self._provider.generate_structured(messages, EnrichmentHints)

    def heuristic(self, catalog: DatabaseCatalog) -> SemanticGraph:
        entities: list[Entity] = []
        relationships: list[Relationship] = []
        metrics: list[MetricDefinition] = []

        # A table needs a primary key to be a business entity we can trust.
        entity_tables = {t.name for t in catalog.tables if _pk_columns(t)}

        for table in catalog.tables:
            pk = _pk_columns(table)
            if not pk:
                continue
            pk_set = set(pk)
            dimension_cols = [
                c
                for c in table.columns
                if c.name not in pk_set and not _is_numeric(c.data_type)
            ]
            entity = Entity(
                name=_humanize(table.name),
                table=table.name,
                primary_key=pk[0],
                dimensions=[_dimension_for(c) for c in dimension_cols],
                description=f"Business entity backed by table {table.name}.",
            )
            entities.append(entity)

            # One lean, always-useful base metric per entity: how many exist.
            # SUM/AVG over specific columns are added by the user (numeric columns
            # are mostly IDs/codes/flags, so auto-summing them all is noise).
            metrics.append(
                MetricDefinition(
                    name=f"{entity.name} Count",
                    description=f"Number of {entity.name} records.",
                    kind=MetricKind.base,
                    entity=entity.name,
                    aggregation=Aggregation.count,
                )
            )

            for fk in table.foreign_keys:
                if fk.references_table in entity_tables:
                    relationships.append(
                        Relationship(
                            from_entity=table.name,
                            to_entity=fk.references_table,
                            from_column=fk.column,
                            to_column=fk.references_column,
                            kind="many_to_one",
                        )
                    )

        return SemanticGraph(
            source_id=catalog.source_id,
            entities=entities,
            metrics=metrics,
            relationships=relationships,
            provenance="heuristic",
        )

    async def _enrich_graph(self, baseline: SemanticGraph) -> SemanticGraph:
        assert self._provider is not None
        messages = [
            Message(
                role=Role.system,
                content=(
                    "You are a data modeling expert building a business semantic "
                    "layer for a BI tool. You are given a heuristic draft semantic "
                    "model derived from a raw database schema. Improve it: give "
                    "entities, dimensions and measures clear business names and "
                    "descriptions, and refine metric definitions so each carries "
                    "real business meaning. Keep every `table`, `column`, "
                    "`expression` and `formula` value EXACTLY as given — only the "
                    "names, descriptions and definitions are yours to improve. Do "
                    "not invent tables or columns that are not in the draft."
                ),
            ),
            Message(
                role=Role.user,
                content=(
                    "Heuristic draft semantic model (JSON):\n"
                    f"{baseline.model_dump_json(indent=2)}\n\n"
                    "Return the improved semantic model as JSON."
                ),
            ),
        ]
        return await self._provider.generate_structured(messages, SemanticGraph)
