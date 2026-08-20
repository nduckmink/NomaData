"""The agent loop, offline — a fake provider returns canned plans, a fake engine
returns canned rows. No tokens spent, so this runs in CI and pins the behaviour
that matters: a bad name is repaired or refused, never swapped silently, and the
"read from" line is built from the model, not the LLM.
"""

from __future__ import annotations

from typing import Any, TypeVar, cast

import pytest
from pydantic import BaseModel

from nomadata.agent.catalog import model_card
from nomadata.agent.runtime import AgentRuntime
from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.models import (
    Aggregation,
    AnalyticalQuery,
    ChatResponse,
    Dimension,
    DimensionKind,
    Entity,
    ExecutionPlan,
    Message,
    MetricDefinition,
    MetricKind,
    ProviderCapabilities,
    QueryPlan,
    QueryResult,
    ResultColumn,
    SemanticGraph,
    TimeSpec,
    ToolCallResponse,
    ToolSpec,
)
from nomadata.query.cube import QueryEngineError

T = TypeVar("T", bound=BaseModel)

FEES = "app.hoc_phi"
STUDENTS = "app.hoc_sinh"


def _graph() -> SemanticGraph:
    return SemanticGraph(
        source_id="scp",
        entities=[
            Entity(
                key=FEES,
                name="Phiếu học phí",
                table="hoc_phi",
                dimensions=[
                    Dimension(name="Trạng thái", column="trang_thai", kind=DimensionKind.string),
                    Dimension(name="Số tiền", column="so_tien", kind=DimensionKind.number),
                    Dimension(
                        name="Ngày thanh toán",
                        column="ngay_thanh_toan",
                        kind=DimensionKind.time,
                    ),
                    Dimension(
                        name="Ghi chú",
                        column="ghi_chu",
                        kind=DimensionKind.string,
                        hidden=True,
                    ),
                ],
            ),
            Entity(
                key=STUDENTS,
                name="Học sinh",
                table="hoc_sinh",
                dimensions=[Dimension(name="Cơ sở", column="co_so", kind=DimensionKind.string)],
            ),
        ],
        metrics=[
            MetricDefinition(
                name="Học phí đã thu",
                kind=MetricKind.base,
                entity_key=FEES,
                aggregation=Aggregation.sum,
                column="so_tien",
                time_dimension="ngay_thanh_toan",
            ),
        ],
        relationships=[],
    )


class FakeProvider(AIProvider):
    """Returns queued QueryPlans in order; records how many times it was asked."""

    def __init__(self, plans: list[QueryPlan]) -> None:
        self._plans = plans
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        raise NotImplementedError

    async def generate_structured(self, messages: list[Message], schema: type[T], **opts: Any) -> T:
        self.calls += 1
        return cast(T, self._plans.pop(0))

    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        raise NotImplementedError


class FakeEngine(QueryEngine):
    def __init__(self, result: QueryResult | None = None, error: str = "") -> None:
        self._result = result or QueryResult(
            columns=[ResultColumn(name="Học phí đã thu", data_type="")],
            rows=[{"Học phí đã thu": 1284500000}],
            row_count=1,
        )
        self._error = error

    async def plan(self, query: AnalyticalQuery, graph: SemanticGraph) -> ExecutionPlan:
        return ExecutionPlan(source_id=graph.source_id)

    async def run(self, query: AnalyticalQuery, graph: SemanticGraph) -> QueryResult:
        if self._error:
            raise QueryEngineError(self._error)
        return self._result


def _plan(**kw: Any) -> QueryPlan:
    return QueryPlan(**kw)


@pytest.mark.asyncio
async def test_a_plain_question_becomes_a_checked_answer() -> None:
    provider = FakeProvider(
        [
            _plan(
                kind="query",
                query=AnalyticalQuery(
                    measures=["Học phí đã thu"],
                    time=TimeSpec(dimension="", range="this_month"),
                ),
            )
        ]
    )
    turn = await AgentRuntime(provider, FakeEngine()).answer("thu bao nhiêu", _graph())

    assert turn.kind == "answer"
    assert turn.answer == "1284500000"
    assert turn.query is not None and turn.query.measures == ["Học phí đã thu"]
    # The trust line is built from the model: names the metric, its aggregation,
    # and the date it defaulted to — none of which came from the LLM.
    assert "Học phí đã thu" in turn.explanation
    assert "sum of so_tien" in turn.explanation
    assert "Ngày thanh toán" in turn.explanation


@pytest.mark.asyncio
async def test_a_bad_name_is_repaired_not_swapped() -> None:
    provider = FakeProvider(
        [
            _plan(kind="query", query=AnalyticalQuery(measures=["Doanh thu"])),  # unknown
            _plan(kind="query", query=AnalyticalQuery(measures=["Học phí đã thu"])),
        ]
    )
    turn = await AgentRuntime(provider, FakeEngine()).answer("doanh thu", _graph())

    assert turn.kind == "answer"
    assert provider.calls == 2  # planned once, repaired once


@pytest.mark.asyncio
async def test_an_unfixable_name_ends_in_a_clear_error() -> None:
    provider = FakeProvider(
        [_plan(kind="query", query=AnalyticalQuery(measures=["Doanh thu"]))] * 3
    )
    turn = await AgentRuntime(provider, FakeEngine()).answer("doanh thu", _graph())

    assert turn.kind == "error"
    assert "Doanh thu" in turn.reason


@pytest.mark.asyncio
async def test_a_clarify_plan_asks_back() -> None:
    provider = FakeProvider([_plan(kind="clarify", clarification="Which fee?")])
    turn = await AgentRuntime(provider, FakeEngine()).answer("fees?", _graph())

    assert turn.kind == "clarify"
    assert turn.clarification == "Which fee?"


@pytest.mark.asyncio
async def test_a_non_data_question_is_refused() -> None:
    provider = FakeProvider([_plan(kind="refuse", reason="Not about this data.")])
    turn = await AgentRuntime(provider, FakeEngine()).answer("weather?", _graph())

    assert turn.kind == "refuse"


@pytest.mark.asyncio
async def test_an_engine_failure_is_a_clean_error_turn() -> None:
    provider = FakeProvider(
        [_plan(kind="query", query=AnalyticalQuery(measures=["Học phí đã thu"]))]
    )
    turn = await AgentRuntime(provider, FakeEngine(error="Cube is down")).answer(
        "thu bao nhiêu", _graph()
    )

    assert turn.kind == "error"
    assert "Cube is down" in turn.reason


def test_model_card_lists_metrics_and_hides_hidden_columns() -> None:
    card = model_card(_graph())
    assert "Học phí đã thu" in card
    assert "Ngày thanh toán" in card  # visible dimension
    assert "Ghi chú" not in card  # hidden dimension is not part of the model
