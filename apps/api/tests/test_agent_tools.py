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
from nomadata.agent.value_cache import VALUES
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
    def __init__(
        self, result: QueryResult | None = None, values: list[object] | None = None
    ) -> None:
        self.ran: list[AnalyticalQuery] = []
        self.asked_values: list[str] = []
        self._values = values or []
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

    async def distinct_values(
        self, member: str, graph: SemanticGraph, *, limit: int = 25
    ) -> list[object]:
        self.asked_values.append(member)
        return self._values


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


def test_the_toolset_excludes_raw_schema() -> None:
    """Raw columns stay out of the answering flow: an agent that can see them
    starts inventing metrics, which is what the semantic layer exists to stop."""
    names = {t.name for t in tool_specs()}
    assert names == {
        "list_metrics",
        "describe_metric",
        "run_query",
        "values_of",
        "reply",
        "ask_back",
        "decline",
    }


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
    assert "Do NOT rank or total from this list" in payload["note"]

    # The facts it would otherwise infer from the fragment, computed over all of
    # it: without these, "the biggest is CS49" is true only of the first 50 rows.
    over_all = payload["over_all_rows"]["Số học sinh"]
    assert over_all["total"] == sum(range(120))
    assert over_all["top"][0] == {"Cơ sở": "CS119", "Số học sinh": 119}


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
async def test_asking_back_is_the_model_own_words() -> None:
    """No second call to label what it just wrote: it labelled the turn by
    choosing the tool, and the question it typed is the question shown."""
    provider = ScriptedProvider([_call("ask_back", question="Doanh thu nào?")])

    turn = await AgentRuntime(provider, FakeEngine()).answer("cho tôi xem doanh thu", _graph())

    assert turn.kind == "clarify"
    assert turn.clarification == "Doanh thu nào?"
    assert turn.usage.llm_calls == 1


@pytest.mark.asyncio
async def test_declining_is_the_model_own_words() -> None:
    provider = ScriptedProvider([_call("decline", reason="Tôi chỉ đọc dữ liệu.")])

    turn = await AgentRuntime(provider, FakeEngine()).answer("xoá hết giao dịch", _graph())

    assert turn.kind == "refuse"
    assert turn.reason == "Tôi chỉ đọc dữ liệu."


@pytest.mark.asyncio
async def test_a_greeting_is_a_reply_not_a_clarification() -> None:
    """A greeting is neither a clarification nor a refusal. Forcing those two
    labels onto one is how "xin chào" reached the user framed as a question it
    had to resolve before anything could happen."""
    provider = ScriptedProvider([_call("reply", text="Xin chào! Tôi trả lời về dữ liệu này.")])

    turn = await AgentRuntime(provider, FakeEngine()).answer("xin chào", _graph())

    assert turn.kind == "reply"
    assert turn.answer == "Xin chào! Tôi trả lời về dữ liệu này."


@pytest.mark.asyncio
async def test_prose_with_no_tool_call_is_still_kept() -> None:
    """The fallback, not a supported ending: this model writes four languages
    into one paragraph when it answers in prose. Its words are kept anyway —
    dropping them would leave the user with an empty turn."""
    provider = ScriptedProvider([ToolCallResponse(content="Xin chào!")])

    turn = await AgentRuntime(provider, FakeEngine()).answer("xin chào", _graph())

    assert turn.kind == "reply"
    assert turn.answer == "Xin chào!"


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


# ----------------------------------------------------------------------
# values_of
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_values_of_reports_what_is_actually_stored() -> None:
    """The model names a column called Trạng thái; it does not say whether the
    rows read COMPLETED, completed or 3. Filtering on the wrong one returns
    nothing, which reads exactly like a real answer of zero."""
    VALUES.clear()
    engine = FakeEngine(values=["COMPLETED", "REJECTED"])
    box = ToolBox(_graph(), engine)

    out = await box.run("values_of", {"dimension": "Trạng thái"})

    assert "COMPLETED" in out and "REJECTED" in out
    assert engine.asked_values == ["hoc_phi.trang_thai"]


@pytest.mark.asyncio
async def test_a_second_ask_is_answered_from_memory() -> None:
    """Values change with the schema, not with the question — one query an hour
    rather than a round trip in front of every filter."""
    VALUES.clear()
    engine = FakeEngine(values=["COMPLETED"])
    box = ToolBox(_graph(), engine)

    await box.run("values_of", {"dimension": "Trạng thái"})
    await box.run("values_of", {"dimension": "Trạng thái"})

    assert len(engine.asked_values) == 1


@pytest.mark.asyncio
async def test_an_ambiguous_dimension_is_asked_about_not_guessed() -> None:
    VALUES.clear()
    graph = _graph()
    graph.entities[1].dimensions.append(
        Dimension(name="Trạng thái", column="trang_thai", kind=DimensionKind.string)
    )
    box = ToolBox(graph, FakeEngine(values=["X"]))

    out = await box.run("values_of", {"dimension": "Trạng thái"})

    assert "More than one table" in out


@pytest.mark.asyncio
async def test_values_that_cannot_be_read_do_not_break_the_turn() -> None:
    """A value list is a convenience. Losing it must not lose the question."""
    VALUES.clear()
    box = ToolBox(_graph(), FakeEngine(values=[]))

    out = await box.run("values_of", {"dimension": "Trạng thái"})

    assert "Could not read the values" in out


@pytest.mark.asyncio
async def test_the_trust_line_says_what_was_filtered_out() -> None:
    """A filtered count and an unfiltered one produced the same sentence and
    different numbers. The line exists so a reader can check the figure; it has
    to mention the half of the query that changed it."""
    provider = ScriptedProvider(
        [
            _call(
                "run_query",
                measures=["Học phí đã thu"],
                filters=[{"field": "Trạng thái", "operator": "eq", "value": "COMPLETED"}],
            )
        ]
    )

    turn = await AgentRuntime(provider, FakeEngine()).answer("học phí đã thu", _graph())

    assert turn.explanation.endswith("where Trạng thái eq COMPLETED.")
