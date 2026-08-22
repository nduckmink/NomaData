"""Semantic suggester tests — heuristic baseline, profiling, AI enrichment.

The load-bearing test here is ``test_enrichment_never_orphans_metrics``: an AI
rename used to leave every metric pointing at a name that no longer existed,
which compiled to a Cube model with zero measures while the UI reported a
successful publish.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.models import (
    Aggregation,
    ChatResponse,
    ColumnInfo,
    ColumnProfile,
    DatabaseCatalog,
    DimensionKind,
    EnrichmentHints,
    ForeignKey,
    Message,
    MetricDefinition,
    MetricKind,
    Origin,
    Provenance,
    ProviderCapabilities,
    SemanticGraph,
    TableInfo,
    ToolCallResponse,
    ToolSpec,
)
from nomadata.query.cube_schema import build_cube_model
from nomadata.semantic.suggester import (
    SemanticSuggester,
    entity_key,
    merge_preserving_edits,
)

T = TypeVar("T", bound=BaseModel)

CUSTOMERS = entity_key("public", "customers")
ORDERS = entity_key("public", "orders")


def _catalog() -> DatabaseCatalog:
    return DatabaseCatalog(
        source_id="shop",
        tables=[
            TableInfo(
                name="customers",
                columns=[
                    ColumnInfo(name="id", data_type="int", is_primary_key=True),
                    ColumnInfo(name="name", data_type="varchar"),
                    ColumnInfo(name="created_at", data_type="datetime"),
                ],
                primary_key=["id"],
            ),
            TableInfo(
                name="orders",
                columns=[
                    ColumnInfo(name="id", data_type="int", is_primary_key=True),
                    ColumnInfo(name="customer_id", data_type="int"),
                    ColumnInfo(name="amount", data_type="decimal"),
                    ColumnInfo(name="status", data_type="varchar"),
                    ColumnInfo(name="note", data_type="text"),
                ],
                primary_key=["id"],
                foreign_keys=[
                    ForeignKey(
                        column="customer_id",
                        references_table="customers",
                        references_column="id",
                    )
                ],
            ),
            # No primary key → not a business entity.
            TableInfo(
                name="event_log",
                columns=[ColumnInfo(name="payload", data_type="text")],
            ),
        ],
    )


def _profiles() -> dict[tuple[str, str], ColumnProfile]:
    return {
        ("orders", "status"): ColumnProfile(
            table="orders",
            column="status",
            distinct_count=3,
            sample_values=["NEW", "PAID", "CANCELLED"],
            is_categorical=True,
        ),
        ("orders", "note"): ColumnProfile(
            table="orders",
            column="note",
            distinct_count=9_812,
            is_categorical=False,
        ),
        ("orders", "amount"): ColumnProfile(
            table="orders", column="amount", distinct_count=4_210, is_categorical=False
        ),
    }


# ----------------------------------------------------------------------
# Heuristic baseline
# ----------------------------------------------------------------------


async def test_heuristic_builds_entities_keyed_by_schema_and_table() -> None:
    graph = SemanticSuggester().heuristic(_catalog())

    assert graph.source_id == "shop"
    assert graph.provenance == "heuristic"
    assert {e.key for e in graph.entities} == {CUSTOMERS, ORDERS}
    # Metrics reference the immutable key, never the display name.
    assert {m.entity_key for m in graph.metrics} == {CUSTOMERS, ORDERS}
    assert all(m.aggregation == Aggregation.count for m in graph.metrics)


async def test_heuristic_reports_skipped_tables() -> None:
    graph = SemanticSuggester().heuristic(_catalog())
    # A table that silently vanishes is worse than one the user is told about.
    assert graph.skipped_tables == [{"table": "event_log", "reason": "no_primary_key"}]


async def test_heuristic_maps_foreign_keys_to_relationships_by_key() -> None:
    graph = SemanticSuggester().heuristic(_catalog())
    assert len(graph.relationships) == 1
    rel = graph.relationships[0]
    assert rel.from_entity_key == ORDERS
    assert rel.to_entity_key == CUSTOMERS
    assert rel.from_column == "customer_id"
    assert rel.kind == "many_to_one"


async def test_dimension_kind_comes_from_the_catalog_not_the_name() -> None:
    graph = SemanticSuggester().heuristic(_catalog())
    customers = next(e for e in graph.entities if e.table == "customers")
    kinds = {d.column: d.kind for d in customers.dimensions}
    assert kinds["created_at"] == DimensionKind.time
    assert kinds["name"] == DimensionKind.string


async def test_profiling_decides_which_columns_are_useful_dimensions() -> None:
    graph = SemanticSuggester().heuristic(_catalog(), profiles=_profiles())
    orders = next(e for e in graph.entities if e.table == "orders")
    dims = {d.column: d for d in orders.dimensions}

    # Low-cardinality text is a real dimension, and its values are kept for the
    # filter picker.
    assert dims["status"].hidden is False
    assert dims["status"].sample_values == ["NEW", "PAID", "CANCELLED"]
    # Nearly-unique free text is useless to group by.
    assert dims["note"].hidden is True
    # A numeric foreign key is a dimension despite being a number — the old rule
    # ("drop every numeric column") lost these entirely.
    assert dims["customer_id"].hidden is False
    assert dims["customer_id"].kind == DimensionKind.number


async def test_scope_limits_the_model_to_chosen_tables() -> None:
    graph = SemanticSuggester().heuristic(_catalog(), tables=["orders"])
    assert {e.table for e in graph.entities} == {"orders"}
    # A join to a table outside the scope cannot be executed, so it is not kept.
    assert graph.relationships == []


# ----------------------------------------------------------------------
# AI enrichment
# ----------------------------------------------------------------------


class _EnrichProvider(AIProvider):
    """Renames everything it is shown, like a real enrichment pass would."""

    def __init__(
        self,
        entities: dict[str, str],
        metrics: dict[str, str],
        unmeasured: set[str] | None = None,
    ) -> None:
        self._entities = entities
        self._metrics = metrics
        self._unmeasured = unmeasured or set()
        self.calls = 0

    @property
    def name(self) -> str:
        return "enrich-fake"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(structured_output=True)

    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        raise NotImplementedError

    async def generate_structured(self, messages: list[Message], schema: type[T], **opts: Any) -> T:
        self.calls += 1
        return schema.model_validate(
            {
                "entities": [
                    {
                        "key": key,
                        "name": name,
                        "description": f"{name} description.",
                        "measurable": key not in self._unmeasured,
                    }
                    for key, name in self._entities.items()
                ],
                "metrics": [
                    {"id": mid, "name": name, "definition": f"{name} definition."}
                    for mid, name in self._metrics.items()
                ],
            }
        )

    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        raise NotImplementedError


async def test_enrichment_never_orphans_metrics() -> None:
    """Regression: renaming entities must not detach their metrics.

    Metrics used to store the entity's *display name*; enrichment rewrote that
    name and left every metric pointing at a value no entity had. The Cube
    compiler then dropped all of them and published a model with no measures.
    """
    suggester = SemanticSuggester(
        _EnrichProvider({CUSTOMERS: "Khách hàng", ORDERS: "Đơn hàng"}, {})
    )
    graph = suggester.heuristic(_catalog(), profiles=_profiles())

    out = await suggester.enrich_batched(graph)

    assert {e.name for e in out.entities} == {"Khách hàng", "Đơn hàng"}
    live_keys = {e.key for e in out.entities}
    assert all(m.entity_key in live_keys for m in out.metrics)

    # The real symptom, asserted directly: the compiled model still measures.
    model = build_cube_model(out)
    assert sum(len(cube["measures"]) for cube in model["cubes"]) == len(out.metrics)


async def test_enrichment_refreshes_default_count_labels() -> None:
    suggester = SemanticSuggester(_EnrichProvider({ORDERS: "Đơn hàng"}, {}))
    graph = suggester.heuristic(_catalog(), tables=["orders"])

    out = await suggester.enrich_batched(graph)

    count = next(m for m in out.metrics if m.entity_key == ORDERS)
    assert count.name == "Đơn hàng Count"  # follows the renamed entity


async def test_enrichment_leaves_user_owned_text_alone() -> None:
    suggester = SemanticSuggester(_EnrichProvider({CUSTOMERS: "AI name", ORDERS: "AI name"}, {}))
    graph = suggester.heuristic(_catalog())
    entities = [
        e.model_copy(update={"name": "My Orders", "provenance": Provenance(origin=Origin.user)})
        if e.key == ORDERS
        else e
        for e in graph.entities
    ]
    graph = graph.model_copy(update={"entities": entities})

    out = await suggester.enrich_batched(graph)

    by_key = {e.key: e for e in out.entities}
    assert by_key[ORDERS].name == "My Orders"  # human owns it
    assert by_key[CUSTOMERS].name == "AI name"  # still a default


async def test_enrichment_respects_a_locked_object() -> None:
    suggester = SemanticSuggester(_EnrichProvider({ORDERS: "AI name"}, {}))
    graph = suggester.heuristic(_catalog(), tables=["orders"])
    entities = [
        e.model_copy(update={"provenance": Provenance(origin=Origin.ai, locked=True)})
        for e in graph.entities
    ]
    graph = graph.model_copy(update={"entities": entities})

    out = await suggester.enrich_batched(graph)

    assert out.entities[0].name == "Orders"  # pinned, so untouched


async def test_plain_counts_are_not_sent_for_naming() -> None:
    """A "<Entity> Count" is already correct; asking the model to rename it
    doubled the cost of every build for no gain."""
    provider = _EnrichProvider({ORDERS: "Đơn hàng"}, {})
    suggester = SemanticSuggester(provider)
    graph = suggester.heuristic(_catalog(), tables=["orders"])

    await suggester.enrich_batched(graph)

    sent = provider.calls
    assert sent == 1  # one batch for the entity, none extra for its count metric


async def test_enrich_batched_noop_without_provider() -> None:
    suggester = SemanticSuggester(None)
    graph = suggester.heuristic(_catalog())
    out = await suggester.enrich_batched(graph)
    assert out is graph


async def test_enrichment_hint_payload_matches_by_key(monkeypatch: Any) -> None:
    """The prompt must carry the stable identifiers back and forth."""
    provider = _EnrichProvider({ORDERS: "Đơn hàng"}, {})
    suggester = SemanticSuggester(provider)
    graph = suggester.heuristic(_catalog(), tables=["orders"])

    hints: EnrichmentHints = await suggester.enrich_hints(graph)

    assert [e.key for e in hints.entities] == [ORDERS]


# ----------------------------------------------------------------------
# Rebuild without losing work
# ----------------------------------------------------------------------


async def test_regenerate_keeps_reviewed_work() -> None:
    suggester = SemanticSuggester()
    previous = suggester.heuristic(_catalog())
    previous = previous.model_copy(
        update={
            "entities": [
                e.model_copy(
                    update={
                        "name": "Đơn hàng",
                        "provenance": Provenance(origin=Origin.user),
                    }
                )
                if e.key == ORDERS
                else e
                for e in previous.entities
            ],
            "metrics": [
                *previous.metrics,
                MetricDefinition(
                    name="Doanh thu",
                    kind=MetricKind.base,
                    entity_key=ORDERS,
                    aggregation=Aggregation.sum,
                    column="amount",
                    provenance=Provenance(origin=Origin.user),
                ),
            ],
        }
    )

    rebuilt = suggester.heuristic(_catalog(), previous=previous)

    by_key = {e.key: e for e in rebuilt.entities}
    assert by_key[ORDERS].name == "Đơn hàng"  # rename survived the rebuild
    assert any(m.name == "Doanh thu" for m in rebuilt.metrics)  # so did the metric
    # And no duplicate Count metric was appended for the same entity.
    counts = [m for m in rebuilt.metrics if m.entity_key == ORDERS and m.column is None]
    assert len(counts) == 1


async def test_merge_drops_entities_whose_table_disappeared() -> None:
    suggester = SemanticSuggester()
    previous = suggester.heuristic(_catalog())
    fresh = suggester.heuristic(_catalog(), tables=["customers"])

    merged = merge_preserving_edits(fresh, previous)

    assert {e.key for e in merged.entities} == {CUSTOMERS}
    assert all(m.entity_key == CUSTOMERS for m in merged.metrics)


# ----------------------------------------------------------------------
# Legacy graphs
# ----------------------------------------------------------------------


async def test_legacy_graph_is_upgraded_on_load() -> None:
    """Models saved before entities had keys must keep working."""
    legacy = {
        "source_id": "shop",
        "entities": [
            {"name": "Orders", "table": "orders", "primary_key": "id", "dimensions": []},
            {
                "name": "Customers",
                "table": "customers",
                "primary_key": "id",
                "dimensions": [],
            },
        ],
        "metrics": [
            {"name": "Orders Count", "kind": "base", "entity": "Orders", "aggregation": "count"}
        ],
        "relationships": [
            {
                "from_entity": "orders",
                "to_entity": "customers",
                "from_column": "customer_id",
                "to_column": "id",
            }
        ],
    }

    graph = SemanticGraph.model_validate(legacy)

    keys = {e.key for e in graph.entities}
    assert graph.metrics[0].entity_key in keys
    assert graph.relationships[0].from_entity_key in keys
    assert graph.relationships[0].to_entity_key in keys


async def test_a_table_nobody_measures_loses_its_row_count() -> None:
    """The heuristic gives every table a count because counting rows is
    mechanical. Whether a count of `role_user` is a number anyone asks for is a
    judgement, and 122 of those buried the 16 metrics somebody designed."""
    suggester = SemanticSuggester(
        _EnrichProvider({CUSTOMERS: "Khách hàng", ORDERS: "Đơn hàng"}, {}, unmeasured={CUSTOMERS})
    )
    graph = suggester.heuristic(_catalog(), profiles=_profiles())

    out = await suggester.enrich_batched(graph)

    assert {m.entity_key for m in out.metrics} == {ORDERS}
    # The table itself stays: nobody counts it, everybody slices by it.
    assert {e.key for e in out.entities} == {CUSTOMERS, ORDERS}


async def test_silence_is_not_a_no() -> None:
    """A batch that failed, or a model that omitted the field, must not be read
    as saying every table is worthless."""
    suggester = SemanticSuggester(_EnrichProvider({}, {}))
    graph = suggester.heuristic(_catalog(), profiles=_profiles())

    out = await suggester.enrich_batched(graph)

    assert {m.entity_key for m in out.metrics} == {CUSTOMERS, ORDERS}
