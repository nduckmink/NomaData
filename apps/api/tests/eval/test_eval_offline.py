"""The offline half of the eval set.

The plan (§C4) splits evaluation in two:

- **offline** (this file, runs in CI): a fake provider returns the *gold* plan
  for each question, and the runtime resolves and translates it against a fixed
  model. It does not measure the model's judgement — it measures the harness:
  every gold query in ``questions.json`` must actually resolve against the model
  (a question expecting a metric that isn't there turns this red), and
  clarify/refuse pass straight through. Zero tokens.
- **live** (``live.py``, run by hand): the real model answers, and its query is
  compared to the gold one to score mapping quality. Not a CI gate.

Keeping the gold plan and the expectation in one JSON file means a new eval case
is a data edit, not code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from pydantic import BaseModel

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
    ToolCallResponse,
    ToolSpec,
)

T = TypeVar("T", bound=BaseModel)

_CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "questions.json").read_text(encoding="utf-8")
)

TXN = "app.transactions"


def _graph() -> SemanticGraph:
    """A small earned-wage-access model the offline questions are written for."""
    return SemanticGraph(
        source_id="scp",
        entities=[
            Entity(
                key=TXN,
                name="Transaction",
                table="transactions",
                dimensions=[
                    Dimension(name="Status", column="status", kind=DimensionKind.string),
                    Dimension(name="Amount", column="request_amount", kind=DimensionKind.number),
                    Dimension(
                        name="Transaction Date",
                        column="transaction_date",
                        kind=DimensionKind.time,
                    ),
                ],
            )
        ],
        metrics=[
            MetricDefinition(
                name="Advance Amount",
                kind=MetricKind.base,
                entity_key=TXN,
                aggregation=Aggregation.sum,
                column="request_amount",
                time_dimension="transaction_date",
            ),
            MetricDefinition(
                name="Transaction Count",
                kind=MetricKind.base,
                entity_key=TXN,
                aggregation=Aggregation.count,
                time_dimension="transaction_date",
            ),
        ],
        relationships=[],
    )


class _Provider(AIProvider):
    def __init__(self, plan: QueryPlan) -> None:
        self._plan = plan

    @property
    def name(self) -> str:
        return "gold"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        raise NotImplementedError

    async def generate_structured(self, messages: list[Message], schema: type[T], **opts: Any) -> T:
        return cast(T, self._plan)

    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        raise NotImplementedError


class _Engine(QueryEngine):
    async def plan(self, query: AnalyticalQuery, graph: SemanticGraph) -> ExecutionPlan:
        return ExecutionPlan(source_id=graph.source_id)

    async def run(self, query: AnalyticalQuery, graph: SemanticGraph) -> QueryResult:
        return QueryResult(
            columns=[ResultColumn(name="value", data_type="")],
            rows=[{"value": 1}],
            row_count=1,
        )


@pytest.mark.parametrize("case", _CASES, ids=[c["question"] for c in _CASES])
@pytest.mark.asyncio
async def test_gold_case_resolves_as_expected(case: dict[str, Any]) -> None:
    plan = QueryPlan.model_validate(case["plan"])
    turn = await AgentRuntime(_Provider(plan), _Engine()).answer(case["question"], _graph())
    expect = case["expect"]

    assert turn.kind == expect["kind"], turn.reason or turn.clarification
    if expect["kind"] != "answer":
        return

    assert turn.query is not None
    assert set(turn.query.measures) == set(expect["measures"])
    if "dimensions" in expect:
        assert set(turn.query.dimensions) == set(expect["dimensions"])
    if "range" in expect:
        assert turn.query.time is not None
        assert turn.query.time.range == expect["range"]
