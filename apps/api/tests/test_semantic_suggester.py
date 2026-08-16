"""Semantic suggester tests — heuristic baseline and AI-enrichment fallback."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.models import (
    Aggregation,
    ChatResponse,
    ColumnInfo,
    DatabaseCatalog,
    ForeignKey,
    Message,
    MetricKind,
    ProviderCapabilities,
    SemanticGraph,
    TableInfo,
    ToolCallResponse,
    ToolSpec,
)
from nomadata.semantic.suggester import SemanticSuggester

T = TypeVar("T", bound=BaseModel)


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


async def test_heuristic_builds_entities_and_base_metrics() -> None:
    graph = SemanticSuggester().heuristic(_catalog())

    assert graph.source_id == "shop"
    assert graph.provenance == "heuristic"

    names = {e.table for e in graph.entities}
    assert names == {"customers", "orders"}  # event_log skipped (no PK)

    orders = next(e for e in graph.entities if e.table == "orders")
    # status/varchar → a dimension; id (PK) excluded.
    dim_cols = {d.column for d in orders.dimensions}
    assert "status" in dim_cols
    assert "id" not in dim_cols

    # Lean baseline: one COUNT metric per entity, no auto SUM-per-column noise.
    assert all(m.aggregation == Aggregation.count for m in graph.metrics)
    assert {m.entity for m in graph.metrics} == {"Customers", "Orders"}


async def test_heuristic_maps_foreign_keys_to_relationships() -> None:
    graph = SemanticSuggester().heuristic(_catalog())
    assert len(graph.relationships) == 1
    rel = graph.relationships[0]
    assert rel.from_entity == "orders"
    assert rel.to_entity == "customers"
    assert rel.from_column == "customer_id"
    assert rel.kind == "many_to_one"


async def test_heuristic_adds_count_metric_per_entity() -> None:
    graph = SemanticSuggester().heuristic(_catalog())
    orders_count = next(m for m in graph.metrics if m.name == "Orders Count")
    assert orders_count.kind == MetricKind.base
    assert orders_count.aggregation == Aggregation.count
    assert orders_count.entity == "Orders"
    assert orders_count.column is None


class _FakeProvider(AIProvider):
    """Returns a fixed enriched graph — stands in for a real LLM."""

    def __init__(self, graph: SemanticGraph | None = None, *, fail: bool = False) -> None:
        self._graph = graph
        self._fail = fail

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(structured_output=True)

    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        raise NotImplementedError

    async def generate_structured(
        self, messages: list[Message], schema: type[T], **opts: Any
    ) -> T:
        if self._fail:
            raise RuntimeError("boom")
        assert self._graph is not None
        return schema.model_validate(self._graph.model_dump())

    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        raise NotImplementedError


async def test_ai_enrichment_sets_provenance_and_pins_source() -> None:
    enriched = SemanticGraph(source_id="anything", provenance="heuristic")
    suggester = SemanticSuggester(_FakeProvider(enriched))
    graph = await suggester.suggest(_catalog(), use_ai=True)
    assert graph.provenance == "ai"
    assert graph.source_id == "shop"  # pinned to the catalog, not the AI's value


async def test_ai_failure_falls_back_to_heuristic() -> None:
    suggester = SemanticSuggester(_FakeProvider(fail=True))
    graph = await suggester.suggest(_catalog(), use_ai=True)
    assert graph.provenance == "heuristic"
    assert {e.table for e in graph.entities} == {"customers", "orders"}


async def test_use_ai_false_skips_provider() -> None:
    suggester = SemanticSuggester(_FakeProvider(fail=True))
    graph = await suggester.suggest(_catalog(), use_ai=False)
    assert graph.provenance == "heuristic"
