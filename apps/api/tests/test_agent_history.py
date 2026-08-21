"""What a follow-up gets to see.

The point of compressing history is that "so với tháng trước?" should be a small
edit to the query that just ran, not a fresh guess from three words. So the
block has to carry the query — and must not carry an old result in a form the
model could mistake for the current answer.
"""

from __future__ import annotations

from nomadata.agent.history import MAX_HISTORY_TURNS, history_block
from nomadata.core.models import (
    AnalyticalQuery,
    ConversationTurn,
    Filter,
    TimeSpec,
)


def _answered(ordinal: int, question: str, **query: object) -> ConversationTurn:
    return ConversationTurn(
        ordinal=ordinal,
        kind="answer",
        question=question,
        query=AnalyticalQuery(**query),  # type: ignore[arg-type]
        answer="1284500000",
    )


def test_no_history_is_no_block() -> None:
    assert history_block([]) == ""


def test_the_block_carries_the_query_to_amend() -> None:
    turns = [
        _answered(
            1,
            "học phí tháng này theo cơ sở",
            measures=["Học phí đã thu"],
            dimensions=["Cơ sở"],
            time=TimeSpec(dimension="", range="this_month"),
        )
    ]

    block = history_block(turns)

    assert "học phí tháng này theo cơ sở" in block
    assert "Học phí đã thu" in block
    assert "this_month" in block
    assert "1284500000" in block
    assert "change only the part the user changed" in block


def test_filters_and_ordering_survive_the_compression() -> None:
    """A follow-up that keeps a filter needs to see the filter."""
    turns = [
        _answered(
            1,
            "top cơ sở",
            measures=["Học phí đã thu"],
            dimensions=["Cơ sở"],
            filters=[Filter(field="Trạng thái", operator="eq", value="Đã thu")],
            order_by=["-Học phí đã thu"],
            limit=10,
        )
    ]

    block = history_block(turns)

    assert "Trạng thái eq Đã thu" in block
    assert "order_by=['-Học phí đã thu']" in block
    assert "limit=10" in block


def test_only_the_last_five_turns_are_carried() -> None:
    """Past five, a question is about something else and the tokens buy nothing."""
    turns = [_answered(i, f"câu {i}", measures=["Học phí đã thu"]) for i in range(1, 9)]

    block = history_block(turns)

    assert "câu 3" not in block
    assert "câu 4" in block  # 4..8 is the last five
    assert block.count("Asked:") == MAX_HISTORY_TURNS


def test_errors_are_not_replayed_as_context() -> None:
    """A turn that failed says nothing about what the user wants next."""
    turns = [
        ConversationTurn(ordinal=1, kind="error", question="hỏng", error="Cube returned 400"),
        _answered(2, "học phí", measures=["Học phí đã thu"]),
    ]

    block = history_block(turns)

    assert "hỏng" not in block
    assert "học phí" in block


def test_a_question_back_is_kept_so_the_answer_makes_sense() -> None:
    """Without it, the next turn's "cái thứ hai" refers to nothing."""
    turns = [
        ConversationTurn(
            ordinal=1,
            kind="clarify",
            question="cho tôi xem doanh thu",
            answer="Doanh thu phí dịch vụ hay phí sau thuế?",
        )
    ]

    block = history_block(turns)

    assert "You asked back: Doanh thu phí dịch vụ hay phí sau thuế?" in block
