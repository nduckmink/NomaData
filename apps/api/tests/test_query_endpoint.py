"""The query endpoint answers in business language, against one source's model.

Closing condition for wave 1: a caller sends metric *names*, not Cube
identifiers, and a name the model does not have comes back as a 400 with the
nearest real one — not as a Cube "member not found" the caller cannot act on.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    Aggregation,
    AnalyticalQuery,
    BusinessContext,
    Dimension,
    DimensionKind,
    Entity,
    ExecutionPlan,
    MetricDefinition,
    MetricKind,
    PublishResult,
    QueryResult,
    ResultColumn,
    SemanticGraph,
    SemanticModelVersion,
)
from nomadata.core.registry import get_registry
from nomadata.main import app
from nomadata.query.cube import build_cube_query
from nomadata.semantic.service import SemanticModelNotFoundError

client = TestClient(app)

FEES = "app.hoc_phi"


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
                    Dimension(name="Số tiền", column="so_tien", kind=DimensionKind.number),
                    Dimension(
                        name="Ngày thanh toán",
                        column="ngay_thanh_toan",
                        kind=DimensionKind.time,
                    ),
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
    """Serves one published graph; anything else has no model."""

    def __init__(self, graph: SemanticGraph | None) -> None:
        self._graph = graph

    async def load(self, source_id: str) -> SemanticGraph:
        if self._graph is None:
            raise SemanticModelNotFoundError(source_id)
        return self._graph

    async def get_draft(self, source_id: str) -> SemanticGraph | None:
        return self._graph

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
    """Compiles for real, but returns canned rows instead of calling Cube."""

    def __init__(self) -> None:
        self.compiled: dict[str, Any] = {}

    async def plan(self, query: AnalyticalQuery, graph: SemanticGraph) -> ExecutionPlan:
        from nomadata.agent.resolver import resolve

        self.compiled = build_cube_query(resolve(query, graph))
        return ExecutionPlan(source_id=graph.source_id, representation=self.compiled)

    async def run(self, query: AnalyticalQuery, graph: SemanticGraph) -> QueryResult:
        await self.plan(query, graph)
        return QueryResult(
            columns=[ResultColumn(name="hoc_phi.Hoc_phi_da_thu", data_type="number")],
            rows=[{"hoc_phi.Hoc_phi_da_thu": 1_284_500_000}],
            row_count=1,
        )


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    """The registry is process-wide, so anything registered here has to be put
    back — otherwise the next test file sees a configured app where it expects
    an unconfigured one."""
    registry = get_registry()
    engine = registry._query_engine  # noqa: SLF001 - test-only save/restore
    semantic = registry._semantic_model  # noqa: SLF001
    yield
    registry._query_engine = engine  # noqa: SLF001
    registry._semantic_model = semantic  # noqa: SLF001


@pytest.fixture
def wired() -> Any:
    """Register a published model and a compiling engine for the request."""
    registry = get_registry()
    engine = _Engine()
    registry.set_query_engine(engine)
    registry.set_semantic_model(_Semantic(_graph()))
    return engine


def test_a_question_asked_in_business_names_runs(wired: _Engine) -> None:
    response = client.post(
        "/api/v1/datasources/scp/query",
        json={
            "measures": ["Học phí đã thu"],
            "dimensions": ["Trạng thái"],
            "time": {"dimension": "", "range": "this_month"},
        },
    )

    assert response.status_code == 200
    assert response.json()["rows"] == [{"hoc_phi.Hoc_phi_da_thu": 1_284_500_000}]
    # Names were translated to members, and the metric's own date filled in.
    assert wired.compiled["measures"] == ["hoc_phi.Hoc_phi_da_thu"]
    assert wired.compiled["timeDimensions"][0]["dimension"] == "hoc_phi.ngay_thanh_toan"


def test_an_unknown_metric_is_a_400_with_a_suggestion(wired: _Engine) -> None:
    """The caller can act on this; a Cube "member not found" they cannot."""
    response = client.post("/api/v1/datasources/scp/query", json={"measures": ["Học phí da thu"]})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "No metric called" in detail
    assert "Học phí đã thu" in detail


def test_a_source_without_a_published_model_says_so() -> None:
    get_registry().set_semantic_model(_Semantic(None))
    get_registry().set_query_engine(_Engine())

    response = client.post("/api/v1/datasources/scp/query", json={"measures": ["Học phí đã thu"]})

    assert response.status_code == 404
    assert "publish one first" in response.json()["detail"]


def test_the_source_timezone_is_stamped_onto_a_relative_period(
    wired: _Engine,
) -> None:
    """The caller says "this month" without a zone; the source's context knows
    which one. Left to Cube it would be UTC, and in UTC+7 the first seven hours
    of every day belong to the day before."""

    class _Contexts:
        async def get(self, name: str) -> BusinessContext:
            return BusinessContext(source_id=name, timezone="Asia/Ho_Chi_Minh")

    app.state.semantic_contexts = _Contexts()
    try:
        client.post(
            "/api/v1/datasources/scp/query",
            json={
                "measures": ["Học phí đã thu"],
                "time": {"dimension": "", "range": "this_month"},
            },
        )
        assert wired.compiled["timezone"] == "Asia/Ho_Chi_Minh"
    finally:
        del app.state.semantic_contexts


def test_a_caller_supplied_timezone_wins(wired: _Engine) -> None:
    class _Contexts:
        async def get(self, name: str) -> BusinessContext:
            return BusinessContext(source_id=name, timezone="Asia/Ho_Chi_Minh")

    app.state.semantic_contexts = _Contexts()
    try:
        client.post(
            "/api/v1/datasources/scp/query",
            json={
                "measures": ["Học phí đã thu"],
                "time": {"dimension": "", "range": "this_month", "timezone": "UTC"},
            },
        )
        assert wired.compiled["timezone"] == "UTC"
    finally:
        del app.state.semantic_contexts


def test_the_row_ceiling_is_always_applied(wired: _Engine) -> None:
    client.post("/api/v1/datasources/scp/query", json={"measures": ["Học phí đã thu"]})
    assert wired.compiled["limit"] > 0
