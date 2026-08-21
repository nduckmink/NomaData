"""The tool loop: the model looks things up, then one query runs.

What these tests hold in place is the boundary. A tool call is not a shortcut
past the resolver — an unknown name is rejected the same way a hand-written
query's would be, and the rejection goes back to the model as text it can act
on, because a tool that raises ends the conversation while a tool that explains
itself lets the model correct course.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar, cast

import pytest
from pydantic import BaseModel

from nomadata.agent.runtime import AgentRuntime
from nomadata.agent.tools import MAX_TOOL_ROWS, ToolBox, tool_specs
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
    ToolCall,
    ToolCallResponse,
    ToolSpec,
)

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
                primary_key="id",
                dimensions=[
                    Dimension(name="Trạng thái", column="trang_thai", kind=DimensionKind.string),
                    Dimension(
                        name="Ngày thanh toán",
                        column="ngay_thanh_toan",
                        kind=DimensionKind.time,
                    ),
                ],
            ),
            Entity(
                key=STUDENTS,
                name="Học sinh",
                table="hoc_sinh",
                primary_key="id",
                dimensions=[Dimension(name="Cơ sở", column="co_so", kind=DimensionKind.string)],
            ),
        ],
        metrics=[
            MetricDefinition(
                name="Học phí đã thu",
                description="Tiền học phí đã vào tài khoản",
                kind=MetricKind.base,
                entity_key=FEES,
                aggregation=Aggregation.sum,
                column="so_tien",
                time_dimension="ngay_thanh_toan",
            ),
            MetricDefinition(
                name="Số học sinh",
                kind=MetricKind.base,
                entity_key=STUDENTS,
                aggregation=Aggregation.count,
            ),
        ],
    )


class FakeEngine(QueryEngine):
    def __init__(self, result: QueryResult | None = None) -> None:
        self.ran: list[AnalyticalQuery] = []
        self._result = result or QueryResult(
            columns=[ResultColumn(name="Học phí đã thu", data_type="")],
            rows=[{"Học phí đã thu": 1284500000}],
            row_count=1,
        )

    async def plan(self, query: AnalyticalQuery, graph: SemanticGraph) -> ExecutionPlan:
        return ExecutionPlan(source_id=graph.source_id)

    async def run(self, query: AnalyticalQuery, graph: SemanticGraph) -> QueryResult:
        self.ran.append(query)
        return self._result


class ScriptedProvider(AIProvider):
    """Replays queued tool-call responses and records what it was sent."""

    def __init__(
        self, responses: list[ToolCallResponse], plans: list[QueryPlan] | None = None
    ) -> None:
        self._responses = responses
        self._plans = plans or []
        self.seen: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(tool_calling=True)

    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        raise NotImplementedError

    async def generate_structured(self, messages: list[Message], schema: type[T], **opts: Any) -> T:
        # Only the non-answer branch reaches this: a reply that ran no query,
        # asked to say whether it was a question back or a refusal.
        assert self._plans, "the tool loop must not fall back to the plan path"
        return cast(T, self._plans.pop(0))

    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        self.seen.append(list(messages))
        return self._responses.pop(0)


def _call(name: str, **arguments: Any) -> ToolCallResponse:
    return ToolCallResponse(
        tool_calls=[ToolCall(id=f"c{len(name)}", name=name, arguments=arguments)]
    )


# ----------------------------------------------------------------------
# The tools themselves
# ----------------------------------------------------------------------


def test_the_toolset_is_three_and_excludes_raw_schema() -> None:
    """Raw columns stay out of the answering flow: an agent that can see them
    starts inventing metrics, which is what the semantic layer exists to stop."""
    names = {t.name for t in tool_specs()}
    assert names == {"list_metrics", "describe_metric", "run_query"}


@pytest.mark.asyncio
async def test_list_metrics_finds_by_topic_without_diacritics() -> None:
    box = ToolBox(_graph(), FakeEngine())
    out = await box.run("list_metrics", {"topic": "hoc phi da thu"})
    assert "Học phí đã thu" in out
    assert "Trạng thái" in out  # the dimensions beside it, so it can slice


@pytest.mark.asyncio
async def test_describe_metric_gives_the_formula_not_the_name() -> None:
    box = ToolBox(_graph(), FakeEngine())
    out = await box.run("describe_metric", {"name": "Học phí đã thu"})
    assert "sum of so_tien" in out
    assert "Ngày thanh toán" in out  # what it is measured over
    assert "Học sinh" not in out  # not another table's columns


@pytest.mark.asyncio
async def test_an_unknown_metric_is_a_message_not_an_exception() -> None:
    """A tool that raises ends the turn; one that explains lets the model fix it."""
    box = ToolBox(_graph(), FakeEngine())
    out = await box.run("describe_metric", {"name": "Doanh thu thuần"})
    assert "No metric called" in out


@pytest.mark.asyncio
async def test_run_query_still_goes_through_the_resolver() -> None:
    engine = FakeEngine()
    box = ToolBox(_graph(), engine)

    out = await box.run("run_query", {"measures": ["Không có thật"]})

    assert "did not work" in out
    assert "No metric called" in out
    assert engine.ran == []  # nothing reached the engine


@pytest.mark.asyncio
async def test_run_query_returns_rows_and_remembers_the_query() -> None:
    engine = FakeEngine()
    box = ToolBox(_graph(), engine)

    out = await box.run("run_query", {"measures": ["Học phí đã thu"]})

    assert json.loads(out)["rows"] == [{"Học phí đã thu": 1284500000}]
    assert box.last_query is not None
    assert box.last_query.measures == ["Học phí đã thu"]  # business names, for "read from"
    assert engine.ran[0].measures == ["hoc_phi.Hoc_phi_da_thu"]  # members, for Cube


@pytest.mark.asyncio
async def test_a_long_result_is_cut_and_says_so() -> None:
    """The model must not total rows it cannot see."""
    rows = [{"Cơ sở": f"CS{i}", "Số học sinh": i} for i in range(120)]
    engine = FakeEngine(
        QueryResult(
            columns=[
                ResultColumn(name="Cơ sở", data_type=""),
                ResultColumn(name="Số học sinh", data_type=""),
            ],
            rows=rows,
            row_count=len(rows),
        )
    )
    box = ToolBox(_graph(), engine)

    payload = json.loads(await box.run("run_query", {"measures": ["Số học sinh"]}))

    assert len(payload["rows"]) == MAX_TOOL_ROWS
    assert payload["row_count"] == 120
    assert "do not claim a total" in payload["note"]


@pytest.mark.asyncio
async def test_an_unknown_tool_name_lists_the_real_ones() -> None:
    box = ToolBox(_graph(), FakeEngine())
    out = await box.run("inspect_schema", {})
    assert "no tool called" in out
    assert "list_metrics" in out


# ----------------------------------------------------------------------
# The loop
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_loop_looks_up_then_queries() -> None:
    engine = FakeEngine()
    provider = ScriptedProvider(
        [
            _call("list_metrics", topic="học phí"),
            _call("run_query", measures=["Học phí đã thu"]),
        ]
    )

    turn = await AgentRuntime(provider, engine).answer("học phí đã thu bao nhiêu", _graph())

    assert turn.kind == "answer"
    assert turn.answer == "1284500000"  # computed from the result, not narrated
    assert turn.explanation.startswith("Read from: Học phí đã thu (sum of so_tien)")
    # The tool result went back with the id that identifies the call it answers.
    tool_messages = [m for m in provider.seen[-1] if m.role.value == "tool"]
    assert tool_messages and tool_messages[0].tool_call_id


@pytest.mark.asyncio
async def test_a_rejected_name_comes_back_for_the_model_to_correct() -> None:
    engine = FakeEngine()
    provider = ScriptedProvider(
        [
            _call("run_query", measures=["Học phí"]),  # not a real name
            _call("run_query", measures=["Học phí đã thu"]),
        ]
    )

    turn = await AgentRuntime(provider, engine).answer("học phí", _graph())

    assert turn.kind == "answer"
    assert any("did not work" in (m.content or "") for m in provider.seen[-1])


@pytest.mark.asyncio
async def test_plain_text_with_no_tool_call_is_a_clarification() -> None:
    provider = ScriptedProvider(
        [ToolCallResponse(content="Model có hai metric doanh thu.")],
        [QueryPlan(kind="clarify", clarification="Doanh thu nào?")],
    )

    turn = await AgentRuntime(provider, FakeEngine()).answer("cho tôi xem doanh thu", _graph())

    assert turn.kind == "clarify"
    assert turn.clarification == "Doanh thu nào?"


@pytest.mark.asyncio
async def test_a_refusal_is_kept_a_refusal() -> None:
    """A refusal written without any agreed marker is still a refusal. Reading a
    prefix let "delete last month's transactions" reach the user as a question."""
    provider = ScriptedProvider(
        [ToolCallResponse(content="Tôi chỉ đọc dữ liệu, không xoá được.")],
        [QueryPlan(kind="refuse", reason="Not about this data.")],
    )

    turn = await AgentRuntime(provider, FakeEngine()).answer("hôm nay trời thế nào", _graph())

    assert turn.kind == "refuse"
    assert turn.reason == "Not about this data."


@pytest.mark.asyncio
async def test_the_loop_gives_up_rather_than_wandering() -> None:
    """Four look-ups and no query is a failure to report, not a loop to keep."""
    provider = ScriptedProvider([_call("list_metrics", topic="x") for _ in range(4)])

    turn = await AgentRuntime(provider, FakeEngine()).answer("gì đó", _graph())

    assert turn.kind == "error"
    assert not provider._responses  # it used its budget and stopped


@pytest.mark.asyncio
async def test_the_headline_reads_the_measure_not_the_first_column() -> None:
    """Cube returns more keys than `columns` lists — the date it grouped by, and
    the granularity of it. Taking the first value printed 2026-05-01 where the
    money belonged: the one number this loop exists to get right, read wrong."""
    engine = FakeEngine(
        QueryResult(
            columns=[ResultColumn(name="hoc_phi.Hoc_phi_da_thu", data_type="number")],
            rows=[
                {
                    "hoc_phi.ngay_thanh_toan.month": "2026-05-01T00:00:00.000",
                    "hoc_phi.Hoc_phi_da_thu": 42,
                    "hoc_phi.ngay_thanh_toan": "2026-05-01T00:00:00.000",
                }
            ],
            row_count=1,
        )
    )
    provider = ScriptedProvider([_call("run_query", measures=["Học phí đã thu"])])

    turn = await AgentRuntime(provider, engine).answer("học phí từng tháng", _graph())

    assert turn.answer == "42"
