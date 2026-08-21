"""What the previous turns look like to the next one.

Not the transcript. Replaying whole turns costs tokens that grow with the
conversation and hands the model an old ``QueryResult`` it can do nothing with —
worse, one it may read as current. Each turn compresses to what the next
question can actually refer to: what was asked, the query that answered it, and
the shape of the result.

That is what makes "so với tháng trước?" cheap. The model is not asked to build
a query from a three-word question; it is shown the query it just ran and told
to change the part that moved.
"""

from __future__ import annotations

from nomadata.core.models import ConversationTurn

#: Turns carried forward. Enough for a follow-up to a follow-up; past that a
#: question is about something else, and the tokens buy nothing.
MAX_HISTORY_TURNS = 5


def history_block(turns: list[ConversationTurn]) -> str:
    """The compressed history to put in front of the next question."""
    kept = [t for t in turns if t.kind in ("answer", "clarify")][-MAX_HISTORY_TURNS:]
    if not kept:
        return ""

    lines = ["EARLIER IN THIS CONVERSATION (oldest first):"]
    for turn in kept:
        lines.append(f'{turn.ordinal}. Asked: "{turn.question}"')
        if turn.kind == "clarify":
            lines.append(f"   You asked back: {turn.answer}")
            continue
        if turn.query is not None:
            lines.append(f"   Ran: {_query_line(turn.query)}")
        if turn.answer:
            lines.append(f"   Result: {turn.answer}")

    lines.append("")
    lines.append(
        "If the new question builds on the last one — another period, another "
        "slice, a filter — start from that query and change only the part the "
        "user changed. Do not rebuild it from nothing, and do not reuse an "
        "earlier result as if it were current: run the query again."
    )
    return "\n".join(lines)


def _query_line(query: object) -> str:
    """One line of a query, in the business names the model speaks."""
    from nomadata.core.models import AnalyticalQuery

    if not isinstance(query, AnalyticalQuery):  # pragma: no cover - defensive
        return str(query)

    parts = [f"measures={query.measures}"]
    if query.dimensions:
        parts.append(f"dimensions={query.dimensions}")
    if query.filters:
        conditions = ", ".join(f"{f.field} {f.operator} {f.value}" for f in query.filters)
        parts.append(f"filters=[{conditions}]")
    if query.time is not None:
        when = query.time.range or f"{query.time.since}..{query.time.until}"
        axis = query.time.dimension or "the metric's own date"
        parts.append(f"time={when} on {axis}")
        if query.time.grain:
            parts.append(f"grain={query.time.grain}")
    if query.order_by:
        parts.append(f"order_by={query.order_by}")
    if query.limit:
        parts.append(f"limit={query.limit}")
    return ", ".join(parts)
