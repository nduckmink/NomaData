"""The three tools the agent may call, and the dispatcher that runs them.

The model card can only ever be a summary: this source publishes 138 metrics
across 122 tables, and the tables one join away from a metric carry 918
dimensions between them. Sending all of that on every question is slow, dear,
and mostly about tables the question never touches — so the card names what
exists nearby and the model asks for the rest.

Three tools do the work — find the metric, understand it, run it — and three
more end the turn: ``reply``, ``ask_back``, ``decline``. Ending through a tool
rather than through plain text is not ceremony. The model in use is reliable
inside a tool argument and degenerates in free-form prose: asked to greet in
Vietnamese it produced four languages in one paragraph and repeated a word
until it ran out. The same model, writing the same sentence into a tool
argument, writes it cleanly. So every turn ends in a tool, and the label comes
from which one it chose instead of from a second call asking what it meant.

``inspect_schema`` — raw database columns — is deliberately absent *here*. An
agent that can see raw columns starts inventing metrics from them, which is
precisely what the semantic layer exists to prevent, and its answer goes
straight to the person asking with nobody in between. The teaching flow planned
for Phase 4 (a person shows the agent how a report is built, the agent proposes
a metric for review) is a different matter: there, raw schema is necessary and a
human publishes the result. Two flows, two toolsets.
"""

from __future__ import annotations

import json
from typing import Any

from nomadata.agent.resolver import (
    QueryValidationError,
    queryable_metrics,
    resolve,
)
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.models import (
    AnalyticalQuery,
    Entity,
    MetricDefinition,
    MetricKind,
    QueryResult,
    SemanticGraph,
    ToolSpec,
)
from nomadata.query.cube_schema import member_map, normalise

#: Rows handed back to the model. It reasons about the shape of a result and
#: reports the headline; it does not need — and cannot afford — every row.
MAX_TOOL_ROWS = 50

#: Metrics one `list_metrics` call may return, most relevant first.
MAX_LISTED_METRICS = 25


def tool_specs() -> list[ToolSpec]:
    """What the model is told it can call."""
    return [
        ToolSpec(
            name="list_metrics",
            description=(
                "Find metrics and the dimensions they can be sliced by. Call this "
                "when the metric a question needs is not in the model card, or to "
                "see what can be sliced by. Search by topic in the user's own "
                "words; omit the topic to list the most substantial metrics."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Words from the question, e.g. 'doanh thu phí dịch vụ'.",
                    }
                },
            },
        ),
        ToolSpec(
            name="describe_metric",
            description=(
                "How one metric is actually calculated: its formula, any filters "
                "built into it, the date it is measured over, and the dimensions "
                "available beside it. Call this before answering a question about "
                "what a metric means, instead of guessing from its name."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "The exact metric name."}},
                "required": ["name"],
            },
        ),
        ToolSpec(
            name="reply",
            description=(
                "Say something to the user that is not an answer from data and "
                "not a question back: a greeting, or what you can help with. "
                "End your turn with it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "Two or three sentences at most, in the language the user wrote in."
                        ),
                    }
                },
                "required": ["text"],
            },
        ),
        ToolSpec(
            name="ask_back",
            description=(
                "Ask the user one short question and end your turn. Use this "
                "when the question is about this data but one thing has to be "
                "settled first — which of two revenue metrics, which period. "
                "Do not use it to answer; do not use it to decline."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "The question, in the language the user asked in. "
                            "Name the candidates you are choosing between."
                        ),
                    }
                },
                "required": ["question"],
            },
        ),
        ToolSpec(
            name="decline",
            description=(
                "Say why you will not answer, and end your turn. Use this when "
                "the question is not about this data at all, or asks to change "
                "data rather than read it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "One or two sentences, in the user's language.",
                    }
                },
                "required": ["reason"],
            },
        ),
        ToolSpec(
            name="run_query",
            description=(
                "Run one query and get the numbers back. Use only names that appear "
                "in the model card or in list_metrics output — never a raw database "
                "column, never an invented name."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "measures": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Metric names. At least one.",
                    },
                    "dimensions": {"type": "array", "items": {"type": "string"}},
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "operator": {
                                    "type": "string",
                                    "enum": [
                                        "eq",
                                        "neq",
                                        "gt",
                                        "gte",
                                        "lt",
                                        "lte",
                                        "in",
                                        "not_in",
                                        "contains",
                                        "set",
                                        "not_set",
                                    ],
                                },
                                "value": {},
                            },
                            "required": ["field", "operator"],
                        },
                    },
                    "time": {
                        "type": "object",
                        "properties": {
                            "dimension": {
                                "type": "string",
                                "description": "Empty string to use the metric's own date.",
                            },
                            "range": {
                                "type": "string",
                                "enum": [
                                    "today",
                                    "yesterday",
                                    "this_week",
                                    "last_week",
                                    "this_month",
                                    "last_month",
                                    "this_quarter",
                                    "last_quarter",
                                    "this_year",
                                    "last_year",
                                    "last_7_days",
                                    "last_30_days",
                                    "last_90_days",
                                    "last_12_months",
                                ],
                            },
                            "grain": {
                                "type": "string",
                                "enum": ["day", "week", "month", "quarter", "year"],
                            },
                        },
                    },
                    "limit": {"type": "integer"},
                    "order_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Names, '-' prefix for descending.",
                    },
                },
                "required": ["measures"],
            },
        ),
    ]


class ToolBox:
    """Runs a named tool against one published graph.

    Holds the last query it ran and its result, because the caller needs both to
    build the answer — the "read from" line is written from the query, and the
    headline number is computed from the result rather than narrated by the
    model, so neither can be misreported.
    """

    def __init__(self, graph: SemanticGraph, engine: QueryEngine) -> None:
        self._graph = graph
        self._engine = engine
        self.last_query: AnalyticalQuery | None = None
        self.last_result: QueryResult | None = None
        self.last_notes: list[str] = []
        #: Set when the model ends the turn without an answer. Its own words,
        #: with its own label — no second call to ask what it meant.
        self.ended: tuple[str, str] | None = None

    async def run(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call and return what the model should read back.

        Every failure comes back as text, never as an exception: a tool that
        raises ends the conversation, while a tool that explains itself lets the
        model correct course — which is the entire point of giving it tools.
        """
        try:
            if name == "reply":
                text = str(arguments.get("text") or "").strip()
                if not text:
                    return "reply needs a `text`. Write what you want to say."
                self.ended = ("reply", text)
                return "Said. Your turn is over."
            if name == "ask_back":
                text = str(arguments.get("question") or "").strip()
                if not text:
                    return "ask_back needs a `question`. Write the question you want to ask."
                self.ended = ("clarify", text)
                return "Asked. Your turn is over."
            if name == "decline":
                text = str(arguments.get("reason") or "").strip()
                if not text:
                    return "decline needs a `reason`. Say why you will not answer."
                self.ended = ("refuse", text)
                return "Declined. Your turn is over."
            if name == "list_metrics":
                return self._list_metrics(str(arguments.get("topic") or ""))
            if name == "describe_metric":
                return self._describe_metric(str(arguments.get("name") or ""))
            if name == "run_query":
                return await self._run_query(arguments)
        except QueryValidationError as exc:
            return f"That did not work: {exc}"
        except Exception as exc:  # noqa: BLE001 - the model reads this and retries
            return f"That did not work: {exc}"
        return (
            f"There is no tool called {name!r}. Available: "
            f"{', '.join(t.name for t in tool_specs())}."
        )

    # ------------------------------------------------------------------
    # list_metrics
    # ------------------------------------------------------------------

    def _list_metrics(self, topic: str) -> str:
        from nomadata.agent.catalog import rank_metrics  # circular at module level

        metrics = rank_metrics(queryable_metrics(self._graph), topic)[:MAX_LISTED_METRICS]
        if not metrics:
            return "This model publishes no metrics that can be queried."

        by_key = {e.key: e for e in self._graph.entities}
        lines = [f"METRICS matching {topic!r}:" if topic else "METRICS:"]
        entities: dict[str, Entity] = {}
        for metric in metrics:
            lines.append(_metric_line(metric, by_key))
            entity = by_key.get(metric.entity_key or "")
            if entity is not None:
                entities[entity.key] = entity

        lines.append("")
        lines.append("DIMENSIONS on the tables those metrics measure:")
        for entity in entities.values():
            names = [d.name for d in entity.dimensions if not d.hidden]
            if names:
                lines.append(f"- {entity.name}: {', '.join(names)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # describe_metric
    # ------------------------------------------------------------------

    def _describe_metric(self, name: str) -> str:
        metric = self._find_metric(name)
        if metric is None:
            return (
                f"No metric called {name!r}. Call list_metrics to see what exists — "
                "do not substitute a different metric."
            )

        by_key = {e.key: e for e in self._graph.entities}
        entity = by_key.get(metric.entity_key or "")
        lines = [f'METRIC "{metric.name}"']
        if metric.description:
            lines.append(f"Means: {metric.description.strip()}")
        if metric.kind == MetricKind.derived:
            lines.append(f"Calculated as: {metric.expression or '(no expression)'}")
        else:
            agg = metric.aggregation.value if metric.aggregation else "?"
            lines.append(f"Calculated as: {agg} of {metric.column or 'rows'}")
        if metric.filters:
            conds = "; ".join(f"{f.field} {f.operator} {f.value}" for f in metric.filters)
            lines.append(f"Counts only rows where: {conds}")
        when = _time_label(metric, by_key)
        lines.append(f"Measured over: {when}" if when else "Measured over: no date — all of it.")
        if entity is not None:
            lines.append(f"Lives on: {entity.name}")
            names = [d.name for d in entity.dimensions if not d.hidden]
            if names:
                lines.append(f"Can be sliced by: {', '.join(names)}")
        return "\n".join(lines)

    def _find_metric(self, name: str) -> MetricDefinition | None:
        mapping = member_map(self._graph)
        metric_id = mapping.metric_id(name)
        if metric_id is not None:
            return next((m for m in self._graph.metrics if m.id == metric_id), None)
        wanted = normalise(name)
        return next((m for m in self._graph.metrics if normalise(m.name) == wanted), None)

    # ------------------------------------------------------------------
    # run_query
    # ------------------------------------------------------------------

    async def _run_query(self, arguments: dict[str, Any]) -> str:
        query = AnalyticalQuery.model_validate(arguments)
        # The same resolver a hand-written query goes through. A tool call is
        # not a privileged path: an unknown name fails here, in business terms,
        # rather than reaching Cube as "member not found".
        resolved = resolve(query, self._graph)
        result = await self._engine.run(resolved, self._graph)

        self.last_query = query
        self.last_result = result
        self.last_notes = resolved.notes

        shown = result.rows[:MAX_TOOL_ROWS]
        payload: dict[str, Any] = {
            "columns": [c.name for c in result.columns],
            "rows": shown,
            "row_count": result.row_count,
        }
        if len(shown) < len(result.rows) or result.truncated:
            # Telling the model not to over-claim is not enough on its own: given
            # 50 of 200 rows it will say "the biggest is X", and X is only the
            # biggest of the 50 it saw. So the facts it would otherwise infer —
            # the totals and the real top rows — are computed here, over every
            # row, and it is told to use these instead of reading the list.
            payload["note"] = (
                f"showing {len(shown)} of {result.row_count} rows. Do NOT rank or "
                "total from this list — it is a fragment. Use `over_all_rows` for "
                "any statement about totals or which is largest, and say in your "
                f"answer that {len(shown)} of {result.row_count} rows are shown."
            )
            payload["over_all_rows"] = _over_all_rows(result)
        return json.dumps(payload, ensure_ascii=False, default=str)


#: How many of the real top rows to compute when the result is cut.
_TOP_N = 5


def _over_all_rows(result: QueryResult) -> dict[str, Any]:
    """Facts computed across every row, for a model that can only see some.

    Numeric columns get a total and the highest rows by that column. This is the
    difference between "the largest branch is X" being true and being true only
    of the fragment that fitted in the context window.
    """
    facts: dict[str, Any] = {"row_count": result.row_count}
    labels = [c.name for c in result.columns if not _numeric(result.rows, c.name)]
    for column in (c.name for c in result.columns if _numeric(result.rows, c.name)):
        values = [row.get(column) for row in result.rows]
        numbers = [float(v) for v in values if isinstance(v, int | float)]
        if not numbers:
            continue
        ranked = sorted(
            (r for r in result.rows if isinstance(r.get(column), int | float)),
            key=lambda r: float(r[column]),
            reverse=True,
        )[:_TOP_N]
        facts[column] = {
            "total": sum(numbers),
            "top": [{k: row.get(k) for k in [*labels, column] if k in row} for row in ranked],
        }
    return facts


def _numeric(rows: list[dict[str, Any]], column: str) -> bool:
    return any(isinstance(row.get(column), int | float) for row in rows)


def _metric_line(metric: MetricDefinition, by_key: dict[str, Entity]) -> str:
    line = f'- "{metric.name}"'
    if metric.description:
        line += f" — {metric.description.strip()}"
    when = _time_label(metric, by_key)
    if when:
        line += f" Measured by: {when}."
    if metric.kind == MetricKind.derived:
        line += " [derived]"
    return line


def _time_label(metric: MetricDefinition, by_key: dict[str, Entity]) -> str:
    entity = by_key.get(metric.entity_key or "")
    if entity is None or not metric.time_dimension:
        return ""
    for dim in entity.dimensions:
        if dim.column == metric.time_dimension:
            return dim.name
    return ""
