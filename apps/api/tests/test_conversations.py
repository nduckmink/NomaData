"""Asking twice: the thread, and what the second question is given.

The endpoint's job here is narrow and worth pinning down. A question with no
thread starts one and says which. A question with a thread gets the earlier
turns handed to the agent. And every turn is written down — including the ones
that failed, because the list of questions the agent could not answer is the
list of what the model is missing, and nothing else records it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    AgentTurn,
    Aggregation,
    AnalyticalQuery,
    ChatResponse,
    Conversation,
    ConversationTurn,
    Dimension,
    DimensionKind,
    Entity,
    ExecutionPlan,
    Message,
    MetricDefinition,
    MetricKind,
    ProviderCapabilities,
    PublishResult,
    QueryResult,
    ResultColumn,
    SemanticGraph,
    SemanticModelVersion,
    ToolCall,
    ToolCallResponse,
    ToolSpec,
)
from nomadata.core.registry import get_registry
from nomadata.main import app

client = TestClient(app)

FEES = "app.hoc_phi"


def _graph() -> SemanticGraph:
    return SemanticGraph(
        source_id="scp",
        version=7,
        entities=[
            Entity(
                key=FEES,
                name="Phiếu học phí",
                table="hoc_phi",
                primary_key="id",
                dimensions=[
                    Dimension(
                        name="Ngày thanh toán", column="ngay_thanh_toan", kind=DimensionKind.time
                    )
                ],
            )
        ],
        metrics=[
            MetricDefinition(
                name="Học phí đã thu",
                kind=MetricKind.base,
                entity_key=FEES,
                aggregation=Aggregation.sum,
                column="so_tien",
                time_dimension="ngay_thanh_toan",
            )
        ],
    )


class _Semantic(SemanticModel):
    async def load(self, source_id: str) -> SemanticGraph:
        return _graph()

    async def get_draft(self, source_id: str) -> SemanticGraph | None:
        return None

    async def save_draft(
        self, graph: SemanticGraph, *, expected_revision: int | None = None
    ) -> SemanticGraph:
        return graph

    async def publish(self, graph: SemanticGraph) -> PublishResult:
        raise NotImplementedError

    async def list_versions(self, source_id: str) -> list[SemanticModelVersion]:
        return []

    async def delete(self, source_id: str) -> int:
        return 0

    async def resolve_metric(self, source_id: str, name: str) -> MetricDefinition:
        raise NotImplementedError


class _Engine(QueryEngine):
    async def plan(self, query: AnalyticalQuery, graph: SemanticGraph) -> ExecutionPlan:
        return ExecutionPlan(source_id=graph.source_id)

    async def run(self, query: AnalyticalQuery, graph: SemanticGraph) -> QueryResult:
        return QueryResult(
            columns=[ResultColumn(name="hoc_phi.Hoc_phi_da_thu", data_type="number")],
            rows=[{"hoc_phi.Hoc_phi_da_thu": 1_284_500_000}],
            row_count=1,
        )


class _Provider(AIProvider):
    """Always runs the one metric, and records the prompt it was given."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(tool_calling=True)

    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        raise NotImplementedError

    async def generate_structured(self, messages: list[Message], schema: type, **opts: Any) -> Any:
        raise NotImplementedError

    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        self.prompts.append(messages[1].content)
        return ToolCallResponse(
            tool_calls=[
                ToolCall(id="c1", name="run_query", arguments={"measures": ["Học phí đã thu"]})
            ],
            usage={"prompt_tokens": 900, "completion_tokens": 40},
        )


class _Conversations:
    """In-memory stand-in with the repository's contract."""

    def __init__(self) -> None:
        self.threads: dict[str, list[AgentTurn]] = {}
        self.sources: dict[str, str] = {}

    async def start(self, source_id: str, title: str = "") -> str:
        conversation_id = str(uuid.uuid4())
        self.threads[conversation_id] = []
        self.sources[conversation_id] = source_id
        return conversation_id

    async def exists(self, conversation_id: str, source_id: str) -> bool:
        return self.sources.get(conversation_id) == source_id

    async def append(self, conversation_id: str, turn: AgentTurn) -> int:
        self.threads[conversation_id].append(turn)
        return len(self.threads[conversation_id])

    async def recent_turns(self, conversation_id: str, limit: int = 5) -> list[ConversationTurn]:
        return [
            ConversationTurn(
                ordinal=i + 1,
                kind=t.kind,
                question=t.question,
                query=t.query,
                answer=t.answer,
            )
            for i, t in enumerate(self.threads[conversation_id][-limit:])
        ]

    async def get(self, conversation_id: str) -> Conversation | None:
        if conversation_id not in self.threads:
            return None
        return Conversation(
            id=conversation_id,
            source_id=self.sources[conversation_id],
            turns=await self.recent_turns(conversation_id, limit=100),
            turn_count=len(self.threads[conversation_id]),
        )

    async def list(self, source_id: str, limit: int = 50) -> list[Conversation]:
        return [
            Conversation(id=cid, source_id=source_id, turn_count=len(turns))
            for cid, turns in self.threads.items()
            if self.sources[cid] == source_id
        ]

    async def delete(self, conversation_id: str) -> bool:
        return self.threads.pop(conversation_id, None) is not None


@pytest.fixture(autouse=True)
def wired() -> Any:
    """Register the fakes for one test and put the process-wide state back."""
    registry = get_registry()
    engine, semantic = registry._query_engine, registry._semantic_model  # noqa: SLF001
    active = registry.active_provider()
    providers = dict(registry._providers)  # noqa: SLF001 - test-only save/restore
    provider = _Provider()
    registry.set_query_engine(_Engine())
    registry.set_semantic_model(_Semantic())
    registry.register_provider("fake", provider)
    registry.set_active_provider(provider)
    store = _Conversations()
    app.state.conversations = store
    yield provider, store
    registry._query_engine = engine  # noqa: SLF001
    registry._semantic_model = semantic  # noqa: SLF001
    registry.set_active_provider(active)
    registry._providers = providers  # noqa: SLF001
    del app.state.conversations


def _ask(question: str, conversation_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"question": question}
    if conversation_id:
        body["conversation_id"] = conversation_id
    response = client.post("/api/v1/datasources/scp/chat", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_question_without_a_thread_starts_one() -> None:
    turn = _ask("học phí đã thu")

    assert turn["kind"] == "answer"
    assert turn["conversation_id"]
    assert turn["ordinal"] == 1


def test_the_second_question_is_shown_the_first(wired: Any) -> None:
    """This is what makes "so với tháng trước?" a small edit rather than a guess."""
    provider, _ = wired
    first = _ask("học phí tháng này")

    second = _ask("còn tháng trước?", first["conversation_id"])

    assert second["ordinal"] == 2
    assert second["conversation_id"] == first["conversation_id"]
    # The follow-up prompt carries the earlier question and the query it ran.
    followup = provider.prompts[-1]
    assert "EARLIER IN THIS CONVERSATION" in followup
    assert "học phí tháng này" in followup
    assert "Học phí đã thu" in followup
    # And the first question's prompt had no history to carry.
    assert "EARLIER IN THIS CONVERSATION" not in provider.prompts[0]


def test_the_turn_records_the_model_version_that_answered() -> None:
    """An answer from v7 cannot be reproduced once v8 is live; say which it was."""
    turn = _ask("học phí đã thu")
    assert turn["model_version"] == 7


def test_the_turn_records_what_it_cost() -> None:
    turn = _ask("học phí đã thu")

    assert turn["usage"]["tokens_in"] == 900
    assert turn["usage"]["tokens_out"] == 40
    assert turn["usage"]["llm_calls"] == 1
    assert turn["usage"]["tool_calls"] == 1
    assert turn["usage"]["latency_ms"] >= 0


def test_a_thread_from_another_source_is_not_continued() -> None:
    """Its history describes metrics whose names mean something else here."""
    first = _ask("học phí đã thu")

    response = client.post(
        "/api/v1/datasources/other/chat",
        json={"question": "gì đó", "conversation_id": first["conversation_id"]},
    )

    assert response.status_code == 404


def test_the_thread_can_be_read_back() -> None:
    first = _ask("học phí đã thu")
    _ask("còn tháng trước?", first["conversation_id"])

    response = client.get(f"/api/v1/datasources/scp/conversations/{first['conversation_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["turn_count"] == 2
    assert [t["question"] for t in body["turns"]] == ["học phí đã thu", "còn tháng trước?"]


def test_threads_are_listed_per_source() -> None:
    _ask("học phí đã thu")

    listed = client.get("/api/v1/datasources/scp/conversations").json()

    assert len(listed) == 1
    assert listed[0]["turn_count"] == 1


def test_the_stream_reports_each_step_then_the_turn() -> None:
    """Ten seconds behind a spinner is the thing being fixed. What streams is
    the work, not the answer being written — the number is computed once the
    query has run, so there is no text being composed a word at a time."""
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream(
        "POST", "/api/v1/datasources/scp/chat/stream", json={"question": "học phí đã thu"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        name = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((name, json.loads(line.removeprefix("data: "))))

    kinds = [name for name, _ in events]
    assert kinds[-1] == "turn"
    assert kinds[:-1] == ["step"] * (len(kinds) - 1)

    labels = [payload["label"] for name, payload in events if name == "step"]
    assert "Reading the semantic model" in labels
    assert any(label.startswith("Running") for label in labels)

    turn = events[-1][1]
    assert turn["kind"] == "answer"
    # The same steps travel with the turn, so reopening the thread shows them.
    assert [s["label"] for s in turn["steps"]] == labels


def test_a_failure_arrives_as_an_event_not_a_cut_stream() -> None:
    """The 200 has already gone out by then; raising would reach the browser as
    a truncated stream with nothing to show for it."""
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream(
        "POST",
        "/api/v1/datasources/scp/chat/stream",
        json={"question": "gì đó", "conversation_id": "not-a-real-thread"},
    ) as response:
        assert response.status_code == 200
        name = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((name, json.loads(line.removeprefix("data: "))))

    assert events[-1][0] == "error"
    assert "not-a-real-thread" in events[-1][1]["detail"]
