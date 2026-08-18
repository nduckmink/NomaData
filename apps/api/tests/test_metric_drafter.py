"""Prompt → metric definition.

The point of these tests is that the model's answer is *never* taken on trust:
an invented column comes back blank with a warning rather than as a
plausible-looking mistake the user would save without noticing.
"""

from __future__ import annotations

from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.models import (
    Aggregation,
    BusinessContext,
    ChatResponse,
    Dimension,
    DimensionKind,
    Entity,
    EntityDraftRequest,
    Message,
    MetricDefinition,
    MetricDraftRequest,
    MetricKind,
    MetricSuggestRequest,
    Origin,
    ProviderCapabilities,
    SemanticGraph,
    ToolCallResponse,
    ToolSpec,
)
from nomadata.semantic.drafter import (
    EntityDrafter,
    MetricDrafter,
    MetricSuggester,
    rank_entities,
)

T = TypeVar("T", bound=BaseModel)

ORDERS = "public.orders"
STUDENTS = "public.students"


class _Provider(AIProvider):
    """Returns a canned proposal and records the prompt it was given."""

    def __init__(self, proposal: dict[str, Any]) -> None:
        self._proposal = proposal
        self.prompt = ""

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(structured_output=True)

    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        raise NotImplementedError

    async def generate_structured(self, messages: list[Message], schema: type[T], **opts: Any) -> T:
        self.prompt = "\n".join(m.content for m in messages)
        return schema.model_validate(self._proposal)

    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        raise NotImplementedError


def _graph() -> SemanticGraph:
    return SemanticGraph(
        source_id="scp",
        entities=[
            Entity(
                key=ORDERS,
                name="Phiếu học phí",
                table="hoc_phi",
                primary_key="id",
                dimensions=[
                    Dimension(
                        name="Trạng thái",
                        column="trang_thai",
                        kind=DimensionKind.string,
                        sample_values=["DA_THU", "CHUA_THU"],
                    ),
                    Dimension(name="Số tiền", column="so_tien", kind=DimensionKind.number),
                    Dimension(
                        name="Ngày thanh toán",
                        column="ngay_thanh_toan",
                        kind=DimensionKind.time,
                    ),
                    Dimension(name="Ngày tạo", column="ngay_tao", kind=DimensionKind.time),
                ],
            ),
            Entity(
                key=STUDENTS,
                name="Học sinh",
                table="hoc_sinh",
                primary_key="id",
                dimensions=[Dimension(name="Lớp", column="lop", kind=DimensionKind.string)],
            ),
        ],
        metrics=[
            MetricDefinition(
                name="Học phí đã thu",
                kind=MetricKind.base,
                entity_key=ORDERS,
                aggregation=Aggregation.sum,
                column="so_tien",
            )
        ],
    )


def _proposal(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Học phí đã thu",
        "entity_key": ORDERS,
        "kind": "base",
        "aggregation": "sum",
        "column": "so_tien",
        "filters": [{"field": "trang_thai", "operator": "eq", "value": "DA_THU"}],
        "time_dimension": "ngay_thanh_toan",
        "format": "currency",
        "description": "Tổng học phí đã thu.",
        "reasoning": "so_tien is the only money column.",
    }
    return {**base, **overrides}


async def test_fills_the_form_from_a_sentence() -> None:
    drafter = MetricDrafter(_Provider(_proposal()))

    result = await drafter.draft(
        MetricDraftRequest(prompt="tổng học phí đã thu theo ngày thanh toán"), _graph()
    )

    metric = result.metric
    assert metric.entity_key == ORDERS
    assert metric.aggregation == Aggregation.sum
    assert metric.column == "so_tien"
    assert metric.time_dimension == "ngay_thanh_toan"
    assert [(f.field, f.operator, f.value) for f in metric.filters] == [
        ("trang_thai", "eq", "DA_THU")
    ]
    assert metric.format == "currency"
    assert metric.provenance.origin == Origin.ai
    assert not result.warnings


async def test_nothing_is_saved_only_returned() -> None:
    """The response is form content, not a persisted change — a new metric gets
    a fresh id and the caller decides whether it ever exists."""
    graph = _graph()
    drafter = MetricDrafter(_Provider(_proposal()))

    result = await drafter.draft(MetricDraftRequest(prompt="doanh thu"), graph)

    assert result.metric.id not in {m.id for m in graph.metrics}
    assert len(graph.metrics) == 1


async def test_invented_column_is_refused_not_guessed() -> None:
    drafter = MetricDrafter(_Provider(_proposal(column="total_amount")))

    result = await drafter.draft(MetricDraftRequest(prompt="doanh thu"), _graph())

    assert result.metric.column is None  # blank, not a plausible-looking mistake
    assert any("total_amount" in w for w in result.warnings)


async def test_sum_over_a_text_column_is_refused() -> None:
    drafter = MetricDrafter(_Provider(_proposal(column="trang_thai")))

    result = await drafter.draft(MetricDraftRequest(prompt="doanh thu"), _graph())

    assert result.metric.column is None
    assert any("numeric" in w for w in result.warnings)


async def test_non_temporal_time_dimension_is_refused() -> None:
    drafter = MetricDrafter(_Provider(_proposal(time_dimension="trang_thai")))

    result = await drafter.draft(MetricDraftRequest(prompt="doanh thu"), _graph())

    assert result.metric.time_dimension is None
    assert any("date/time" in w for w in result.warnings)


async def test_filter_on_an_unknown_column_is_dropped() -> None:
    drafter = MetricDrafter(
        _Provider(_proposal(filters=[{"field": "ghost", "operator": "eq", "value": "X"}]))
    )

    result = await drafter.draft(MetricDraftRequest(prompt="doanh thu"), _graph())

    assert result.metric.filters == []
    assert any("ghost" in w for w in result.warnings)


async def test_unknown_filter_operator_is_dropped_not_downgraded() -> None:
    """Silently turning an unsupported operator into '=' changes the number."""
    drafter = MetricDrafter(
        _Provider(_proposal(filters=[{"field": "trang_thai", "operator": "between", "value": "X"}]))
    )

    result = await drafter.draft(MetricDraftRequest(prompt="doanh thu"), _graph())

    assert result.metric.filters == []
    assert any("between" in w for w in result.warnings)


async def test_unseen_filter_value_is_flagged() -> None:
    drafter = MetricDrafter(
        _Provider(
            _proposal(filters=[{"field": "trang_thai", "operator": "eq", "value": "DA_THUU"}])
        )
    )

    result = await drafter.draft(MetricDraftRequest(prompt="doanh thu"), _graph())

    assert result.metric.filters  # kept: samples are not exhaustive
    assert any("DA_THUU" in w for w in result.warnings)


async def test_editing_keeps_the_id_and_reports_what_changed() -> None:
    graph = _graph()
    existing = graph.metrics[0]
    drafter = MetricDrafter(_Provider(_proposal(name=existing.name, time_dimension="ngay_tao")))

    result = await drafter.draft(
        MetricDraftRequest(prompt="đổi sang tính theo ngày tạo phiếu", base=existing),
        graph,
    )

    assert result.metric.id == existing.id
    assert result.metric.time_dimension == "ngay_tao"
    assert "time_dimension" in result.changed_fields
    assert "name" not in result.changed_fields  # untouched fields aren't highlighted


async def test_business_context_reaches_the_prompt() -> None:
    provider = _Provider(_proposal())
    drafter = MetricDrafter(provider)

    await drafter.draft(
        MetricDraftRequest(prompt="doanh thu"),
        _graph(),
        context=BusinessContext(domain="Quản lý trường học", language="vi"),
    )

    assert "Vietnamese" in provider.prompt
    assert "Quản lý trường học" in provider.prompt


async def test_entity_shortlist_is_lexical_so_big_schemas_stay_cheap() -> None:
    """With 124 tables the whole graph cannot go into the prompt; the right
    entity only has to reach a shortlist the model then picks from."""
    graph = _graph()
    ranked = rank_entities(graph, "tổng học phí theo lớp", limit=1)
    assert ranked[0].key in (ORDERS, STUDENTS)

    ranked = rank_entities(graph, "học sinh theo lop", limit=1)
    assert ranked[0].key == STUDENTS


# ----------------------------------------------------------------------
# Entities — text only, asked for where the user is editing
# ----------------------------------------------------------------------


async def test_entity_draft_returns_business_text() -> None:
    provider = _Provider(
        {
            "name": "Phiếu học phí",
            "description": "Từng khoản học phí của một học sinh.",
            "reasoning": "columns are amount, status and payment date",
        }
    )

    result = await EntityDrafter(provider).draft(
        EntityDraftRequest(prompt="đây là bảng phiếu thu học phí", entity_key=ORDERS),
        _graph(),
    )

    assert result.name == "Phiếu học phí"
    assert result.description.startswith("Từng khoản")
    assert "description" in result.changed_fields
    # The structure was never on the table, so the prompt carries only columns.
    assert "hoc_phi" in provider.prompt
    assert "trang_thai" in provider.prompt


async def test_entity_draft_keeps_current_text_when_the_model_returns_nothing() -> None:
    result = await EntityDrafter(_Provider({"name": "", "description": ""})).draft(
        EntityDraftRequest(prompt="?", entity_key=ORDERS), _graph()
    )

    assert result.name == "Phiếu học phí"  # unchanged, not wiped
    assert result.changed_fields == []
    assert len(result.warnings) == 2


async def test_entity_draft_rejects_an_unknown_entity() -> None:
    with pytest.raises(ValueError, match="No entity"):
        await EntityDrafter(_Provider({})).draft(
            EntityDraftRequest(prompt="x", entity_key="public.nope"), _graph()
        )


# ----------------------------------------------------------------------
# Bulk suggestion — the same checks, applied to a list
# ----------------------------------------------------------------------


def _suggestions(*proposals: dict[str, Any]) -> dict[str, Any]:
    return {"metrics": list(proposals)}


async def test_suggests_metrics_worth_tracking() -> None:
    provider = _Provider(
        _suggestions(
            _proposal(name="Học phí đã thu"),
            _proposal(
                name="Số phiếu",
                aggregation="count",
                column="",
                filters=[],
                time_dimension="",
                format="number",
            ),
        )
    )

    result = await MetricSuggester(provider).suggest(
        MetricSuggestRequest(entity_key=ORDERS), _graph()
    )

    assert [m.name for m in result.metrics] == ["Học phí đã thu", "Số phiếu"]
    assert all(m.entity_key == ORDERS for m in result.metrics)
    assert all(m.provenance.origin == Origin.ai for m in result.metrics)
    assert len(result.reasons) == len(result.metrics)


async def test_a_proposal_that_fails_its_checks_is_dropped_not_shown_blank() -> None:
    """A single hand-written metric can come back with a blank field and a
    warning — the user is looking at it. In a list of five, a half-filled row is
    just noise, so it does not make the cut."""
    provider = _Provider(
        _suggestions(
            _proposal(name="Bịa", column="khong_co_cot"),
            _proposal(name="Học phí đã thu"),
        )
    )

    result = await MetricSuggester(provider).suggest(
        MetricSuggestRequest(entity_key=ORDERS), _graph()
    )

    assert [m.name for m in result.metrics] == ["Học phí đã thu"]
    assert any("Bịa" in w for w in result.warnings)


async def test_a_proposal_the_entity_already_has_is_skipped() -> None:
    """The graph already measures sum(so_tien); proposing it again under a new
    name would quietly create two names for one number."""
    provider = _Provider(_suggestions(_proposal(name="Tổng thu", filters=[])))

    result = await MetricSuggester(provider).suggest(
        MetricSuggestRequest(entity_key=ORDERS), _graph()
    )

    assert result.metrics == []


async def test_suggestion_respects_the_requested_limit() -> None:
    provider = _Provider(_suggestions(*[_proposal(name=f"M{i}", filters=[]) for i in range(6)]))

    result = await MetricSuggester(provider).suggest(
        MetricSuggestRequest(entity_key=ORDERS, limit=2), _graph()
    )

    assert len(result.metrics) <= 2


async def test_suggestion_rejects_an_unknown_entity() -> None:
    with pytest.raises(ValueError, match="No entity"):
        await MetricSuggester(_Provider(_suggestions())).suggest(
            MetricSuggestRequest(entity_key="public.nope"), _graph()
        )
