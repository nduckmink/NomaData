"""Ratios: the half of a semantic model base metrics cannot express.

A total says how much happened. A ratio says whether it went well — recovery
rate, fee as a share of volume, average value per transaction — and that is what
a business steers by. The build produced 138 metrics for this source and not one
of them was a ratio, because the only pass that proposed metrics looked at one
entity's *columns*, and a ratio is made of its *metrics*.

The dangerous failure here is silent: Cube builds a calculated measure inside a
single cube, so a formula naming a metric this entity does not have compiles to
nothing and ships as a metric that is simply absent. Nobody sees an error; the
model just quietly has less in it than it claims.
"""

from __future__ import annotations

from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.models import (
    Aggregation,
    ChatResponse,
    Dimension,
    DimensionKind,
    Entity,
    Message,
    MetricDefinition,
    MetricKind,
    MetricSuggestRequest,
    ProviderCapabilities,
    SemanticGraph,
    ToolCallResponse,
    ToolSpec,
)
from nomadata.semantic.drafter import MetricSuggester

T = TypeVar("T", bound=BaseModel)

ORDERS = "app.orders"


def _graph(metrics: list[MetricDefinition] | None = None) -> SemanticGraph:
    return SemanticGraph(
        source_id="s",
        entities=[
            Entity(
                key=ORDERS,
                name="Đơn hàng",
                table="orders",
                primary_key="id",
                dimensions=[
                    Dimension(name="Ngày đặt", column="ordered_at", kind=DimensionKind.time),
                    Dimension(name="Số tiền", column="amount", kind=DimensionKind.number),
                ],
            )
        ],
        metrics=metrics
        if metrics is not None
        else [
            MetricDefinition(
                name="Tổng doanh thu",
                kind=MetricKind.base,
                entity_key=ORDERS,
                aggregation=Aggregation.sum,
                column="amount",
            ),
            MetricDefinition(
                name="Số đơn hàng",
                kind=MetricKind.base,
                entity_key=ORDERS,
                aggregation=Aggregation.count,
            ),
        ],
    )


class _Provider(AIProvider):
    """Returns the ratio proposals it was constructed with."""

    def __init__(self, proposals: list[dict[str, Any]]) -> None:
        self._proposals = proposals
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(structured_output=True)

    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        raise NotImplementedError

    async def generate_structured(self, messages: list[Message], schema: type[T], **opts: Any) -> T:
        self.calls += 1
        return schema.model_validate({"metrics": self._proposals})

    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_a_ratio_over_two_existing_metrics_is_accepted() -> None:
    provider = _Provider(
        [
            {
                "name": "Giá trị đơn hàng bình quân",
                "kind": "derived",
                "expression": "Tổng doanh thu / Số đơn hàng",
                "description": "Doanh thu trung bình mỗi đơn.",
                "reasoning": "Cho biết đơn hàng đang to lên hay nhỏ đi.",
            }
        ]
    )

    result = await MetricSuggester(provider).suggest_derived(ORDERS, _graph())

    assert len(result.metrics) == 1
    metric = result.metrics[0]
    assert metric.kind == MetricKind.derived
    assert metric.expression == "Tổng doanh thu / Số đơn hàng"
    # No entity_key on purpose: which cube a ratio lives in is worked out from
    # the metrics its formula names, at compile time. Stamping one here would be
    # a second source of truth for the same fact.
    assert metric.entity_key is None


@pytest.mark.asyncio
async def test_a_formula_naming_something_this_entity_lacks_is_dropped() -> None:
    """It would compile to nothing and ship as a metric that is simply absent."""
    provider = _Provider(
        [
            {
                "name": "Tỷ lệ thu hồi",
                "kind": "derived",
                "expression": "Tổng đã thu / Tổng doanh thu",
                "description": "…",
            }
        ]
    )

    result = await MetricSuggester(provider).suggest_derived(ORDERS, _graph())

    assert result.metrics == []
    assert "this table does not have" in result.warnings[0]


@pytest.mark.asyncio
async def test_a_formula_that_combines_nothing_is_dropped() -> None:
    """One metric renamed is not a ratio."""
    provider = _Provider(
        [
            {
                "name": "Doanh thu (nghìn)",
                "kind": "derived",
                "expression": "Tổng doanh thu / 1000",
                "description": "…",
            }
        ]
    )

    result = await MetricSuggester(provider).suggest_derived(ORDERS, _graph())

    assert result.metrics == []


@pytest.mark.asyncio
async def test_an_entity_with_one_metric_costs_no_call() -> None:
    """A ratio needs two numbers; asking about a table that has one is spending
    money to be told nothing."""
    provider = _Provider([])
    graph = _graph(
        [
            MetricDefinition(
                name="Số đơn hàng",
                kind=MetricKind.base,
                entity_key=ORDERS,
                aggregation=Aggregation.count,
            )
        ]
    )

    result = await MetricSuggester(provider).suggest_derived(ORDERS, graph)

    assert result.metrics == []
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_a_ratio_already_defined_is_not_proposed_twice() -> None:
    existing = _graph().metrics + [
        MetricDefinition(
            name="Giá trị đơn hàng bình quân",
            kind=MetricKind.derived,
            entity_key=ORDERS,
            expression="Tổng doanh thu / Số đơn hàng",
        )
    ]
    provider = _Provider(
        [
            {
                "name": "AOV",
                "kind": "derived",
                "expression": "Tổng doanh thu / Số đơn hàng",
                "description": "…",
            }
        ]
    )

    result = await MetricSuggester(provider).suggest_derived(ORDERS, _graph(existing))

    assert result.metrics == []


# ----------------------------------------------------------------------
# The same guard, wherever the proposal came from
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_base_pass_cannot_smuggle_a_cross_table_formula() -> None:
    """This pass is asked for metrics over an entity's columns, and every guard
    in it tested `kind == base` — so a model that answered with a formula walked
    straight through. That is how a ratio spanning two tables reached a
    published model, where Cube compiles it to nothing and nobody is told."""
    provider = _Provider(
        [
            {
                "name": "Quy mô nợ quá hạn trung bình",
                "kind": "derived",
                "expression": "Tổng tiền nợ quá hạn / Số hồ sơ đã duyệt",
                "description": "…",
            }
        ]
    )

    result = await MetricSuggester(provider).suggest(
        MetricSuggestRequest(entity_key=ORDERS), _graph()
    )

    assert result.metrics == []
    assert "does not have" in result.warnings[0]


@pytest.mark.asyncio
async def test_the_base_pass_keeps_a_formula_that_is_sound() -> None:
    """The guard is about what a formula names, not about which pass wrote it."""
    provider = _Provider(
        [
            {
                "name": "Giá trị đơn hàng bình quân",
                "kind": "derived",
                "expression": "Tổng doanh thu / Số đơn hàng",
                "description": "…",
            }
        ]
    )

    result = await MetricSuggester(provider).suggest(
        MetricSuggestRequest(entity_key=ORDERS), _graph()
    )

    assert [m.name for m in result.metrics] == ["Giá trị đơn hàng bình quân"]
