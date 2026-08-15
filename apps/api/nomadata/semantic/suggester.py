"""Draft a SemanticGraph from a DatabaseCatalog.

Two layers, both producing the same artifact:

- **Heuristic baseline** (always runs, no key needed): tables with a primary key
  become entities, numeric columns become measures, categorical/date columns
  become dimensions, foreign keys become relationships, and each entity gets a
  ``<Entity> Count`` metric candidate.
- **AI enrichment** (when an ``AIProvider`` is available): the baseline is handed
  to the model, which returns business names, descriptions and richer metric
  definitions. The AI *proposes* — the output is a draft a human still reviews
  and publishes; on any failure we fall back to the heuristic graph.
"""

from __future__ import annotations

from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.models import (
    ColumnInfo,
    DatabaseCatalog,
    Dimension,
    Entity,
    Measure,
    Message,
    MetricDefinition,
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


def _measures_for(table: TableInfo, numeric_cols: list[ColumnInfo]) -> list[Measure]:
    measures: list[Measure] = []
    for col in numeric_cols:
        measures.append(
            Measure(
                name=f"Total {_humanize(col.name)}",
                expression=f"SUM({table.name}.{col.name})",
                description=f"Sum of {col.name} across {table.name}.",
            )
        )
    return measures


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

    async def enrich(self, graph: SemanticGraph) -> SemanticGraph:
        """AI-improve business names/descriptions/definitions of an existing
        graph, keeping tables/columns/formulas exact. Requires a provider."""
        if self._provider is None:
            raise RuntimeError("No AI provider configured for enrichment.")
        enriched = await self._enrich_graph(graph)
        return enriched.model_copy(
            update={"source_id": graph.source_id, "provenance": "ai"}
        )

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
            numeric_cols = [
                c for c in table.columns if _is_numeric(c.data_type) and c.name not in pk_set
            ]
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
                measures=_measures_for(table, numeric_cols),
                description=f"Business entity backed by table {table.name}.",
            )
            entities.append(entity)

            # A universally useful metric candidate: how many of this entity exist.
            metrics.append(
                MetricDefinition(
                    name=f"{entity.name} Count",
                    definition=f"Number of {entity.name} records.",
                    formula=f"COUNT({table.name}.{pk[0]})",
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
