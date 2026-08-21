"""The agent loop: one question in, one checked answer (or a clear non-answer) out.

Plan-then-execute, not free-form ReAct. The model makes ONE decision — run a
query, ask back, or refuse — and every query it proposes goes through the same
``resolver`` a hand-written one does before anything runs. A resolver error is
fed back for up to two repair turns; past that the agent says it couldn't,
rather than swapping in a different metric to make *something* run (which is how
an agent answers fluently and wrongly).

Two deliberate choices for this first cut:

- **The headline number is computed, not narrated.** ``_summarize`` reads the
  result; there's no second LLM call to describe it, so it cannot misstate it.
- **The "read from" line is built by ``explain``, not the LLM.** A model
  explaining its own query is how it rationalises a wrong one convincingly.
"""

from __future__ import annotations

from nomadata.agent.catalog import model_card
from nomadata.agent.resolver import QueryValidationError, ResolvedQuery, resolve
from nomadata.agent.tools import ToolBox, tool_specs
from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.models import (
    AgentTurn,
    AnalyticalQuery,
    BusinessContext,
    Entity,
    Message,
    MetricDefinition,
    MetricKind,
    QueryPlan,
    QueryResult,
    Role,
    SemanticGraph,
)
from nomadata.query.cube import QueryEngineError
from nomadata.query.cube_schema import normalise
from nomadata.semantic.prompt import context_rules

_MAX_REPAIRS = 2

#: How many times the model may call tools before it has to have an answer.
#: Enough for look-up, correct a rejected name, run — and no room to wander.
_MAX_TOOL_TURNS = 4

_SYSTEM = (
    "You are a careful analytics assistant. Turn the user's question into ONE "
    "query against the semantic model given below.\n"
    "Use ONLY the exact metric and dimension names listed. A metric name is used "
    "on its own — do not prefix it with an entity or table name. Never invent a "
    "name, never write SQL, never reference a raw database column.\n"
    "Return a JSON object with a `kind` field:\n"
    '  - "query": also set `query` with '
    "{measures: [metric names], dimensions: [dimension names], "
    "filters: [{field, operator, value}], time: {dimension, range}, limit, "
    "order_by: [names, '-' prefix for descending]}.\n"
    "      operator is one of: eq, neq, gt, gte, lt, lte, in, not_in, contains, "
    "set, not_set.\n"
    "      time.range is one of: today, yesterday, this_week, last_week, "
    "this_month, last_month, this_quarter, last_quarter, this_year, last_year, "
    "last_7_days, last_30_days, last_90_days, last_12_months — or omit `time` "
    "entirely for all of history.\n"
    '      Set time.dimension to "" to measure by the metric\'s own date column.\n'
    '  - "clarify": set `clarification` to ONE short question, when the request '
    "is ambiguous or asks for something not in the model.\n"
    '  - "refuse": set `reason`, when the question is not about this data.\n"'
    "Never guess a name to make a query run — clarify instead. Return ONLY JSON."
)


_TOOL_SYSTEM = (
    "You are a careful analytics assistant. Answer the user's question about "
    "this data source by calling the tools you have been given.\n"
    "The model card below lists the metrics and the columns of the tables they "
    "measure. It does NOT list the columns of joined tables, and it does not "
    "list every metric — call list_metrics when what you need is not there, "
    "instead of settling for something close.\n"
    "Use ONLY the exact metric and dimension names from the card or from a tool "
    "result. Never invent a name, never write SQL, never name a raw database "
    "column. A metric name is used on its own; a dimension may be written "
    '"Table.Name" when the same name appears on more than one table.\n'
    "Call run_query exactly once, when you know which metric answers the "
    "question. If a tool rejects a name, read what it says and correct it.\n"
    "Before you run anything, compare the user's words against the metric "
    "names. If one metric's name contains those words and the others do not, "
    "that is the metric — use it and do not ask. Ask only when the words stop "
    "short of choosing: a bare 'doanh thu' or 'số tiền' where the model "
    "publishes several. Then name the candidates and ask which is meant, "
    "because picking the likelier one produces a number that looks right and "
    "answers a question nobody asked, and nothing downstream can catch that.\n"
    "When you cannot answer, reply in plain text and call no tool: say what is "
    "ambiguous and ask ONE short question, or say why the question is not about "
    "this data. Never guess a metric to make a query run."
)


_CLASSIFY_SYSTEM = (
    "You answered a question about a data source without running a query. Say "
    "which of the two it was, as JSON:\n"
    '  - {"kind": "refuse", "reason": "..."} when the question is not about this '
    "data at all, or asks to change data rather than read it.\n"
    '  - {"kind": "clarify", "clarification": "..."} when it is about this data '
    "but you need one thing settled first. Keep the question to one sentence.\n"
    "Write the reason or the question in the user's own language. Return ONLY JSON."
)


class AgentRuntime:
    def __init__(self, provider: AIProvider, engine: QueryEngine) -> None:
        self._provider = provider
        self._engine = engine

    async def answer(
        self,
        question: str,
        graph: SemanticGraph,
        *,
        context: BusinessContext | None = None,
    ) -> AgentTurn:
        card = model_card(graph, question=question)
        if self._provider.capabilities.tool_calling:
            return await self._answer_with_tools(question, card, graph, context)
        return await self._answer_from_plan(question, card, graph, context)

    async def _answer_with_tools(
        self,
        question: str,
        card: str,
        graph: SemanticGraph,
        context: BusinessContext | None,
    ) -> AgentTurn:
        """Let the model look things up, then run one query through the resolver.

        The card lists what the metrics measure; everything else — the 62 tables
        one join away and their 918 columns — is a tool call away instead of in
        every prompt. The loop ends the moment a query runs: the number and the
        "read from" line are built from the query that ran, not from the model
        describing what it did, so it has no chance to misreport either.
        """
        box = ToolBox(graph, self._engine)
        messages = [
            Message(role=Role.system, content=_TOOL_SYSTEM + context_rules(context)),
            Message(role=Role.user, content=f"{card}\n\nQuestion: {question}"),
        ]

        for _ in range(_MAX_TOOL_TURNS):
            response = await self._provider.tool_call(messages, tool_specs())

            if not response.tool_calls:
                return await self._non_answer(question, response.content or "", context)

            messages.append(
                Message(
                    role=Role.assistant,
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                output = await box.run(call.name, call.arguments)
                messages.append(
                    Message(role=Role.tool, content=output, tool_call_id=call.id, name=call.name)
                )

            if box.last_result is not None and box.last_query is not None:
                return AgentTurn(
                    kind="answer",
                    question=question,
                    query=box.last_query,
                    result=box.last_result,
                    answer=_summarize(box.last_result),
                    explanation=explain(box.last_query, graph),
                    notes=box.last_notes,
                )

        return AgentTurn(
            kind="error",
            question=question,
            reason="I looked, but couldn't turn that into a query I could run.",
        )

    async def _answer_from_plan(
        self,
        question: str,
        card: str,
        graph: SemanticGraph,
        context: BusinessContext | None,
    ) -> AgentTurn:
        """One structured decision, no tools — for a provider that cannot call them."""
        plan = await self._plan(question, card, context)

        # No fallback text here on purpose. ``QueryPlan`` now rejects a reply
        # whose kind carries nothing, so a clarification is one the model
        # actually wrote — not a sentence we invented to cover a broken reply
        # and then showed the user as if the model had asked it.
        if plan.kind == "clarify":
            return AgentTurn(kind="clarify", question=question, clarification=plan.clarification)
        if plan.kind == "refuse":
            return AgentTurn(kind="refuse", question=question, reason=plan.reason)
        if plan.query is None:  # pragma: no cover - the validator forbids it
            return AgentTurn(
                kind="error",
                question=question,
                reason="The assistant returned a plan with no query.",
            )

        business = plan.query
        resolved: ResolvedQuery | None = None
        for attempt in range(_MAX_REPAIRS + 1):
            try:
                resolved = resolve(business, graph)
                break
            except QueryValidationError as exc:
                if attempt == _MAX_REPAIRS:
                    return AgentTurn(kind="error", question=question, reason=str(exc))
                repair = await self._repair(question, card, business, exc, context)
                if repair.kind == "clarify":
                    return AgentTurn(
                        kind="clarify",
                        question=question,
                        clarification=repair.clarification or str(exc),
                    )
                if repair.kind == "refuse" or repair.query is None:
                    return AgentTurn(
                        kind="refuse" if repair.kind == "refuse" else "error",
                        question=question,
                        reason=repair.reason or str(exc),
                    )
                business = repair.query

        assert resolved is not None  # loop either broke with a value or returned
        resolved = _stamp_timezone(resolved, context)

        try:
            result = await self._engine.run(resolved, graph)
        except QueryEngineError as exc:
            return AgentTurn(kind="error", question=question, query=business, reason=str(exc))

        return AgentTurn(
            kind="answer",
            question=question,
            query=business,
            result=result,
            answer=_summarize(result),
            explanation=explain(business, graph),
            notes=resolved.notes,
        )

    async def _non_answer(
        self, question: str, text: str, context: BusinessContext | None
    ) -> AgentTurn:
        """Decide whether a reply that called no tool is a question or a refusal.

        Reading a prefix out of the text was cheaper and wrong: the model wrote a
        perfectly good refusal of "delete last month's transactions" without the
        agreed marker, and it reached the user as a clarification. Asking it to
        classify what it just wrote costs one call on a branch that is rare by
        construction — every answerable question ends in a tool call instead.
        """
        plan = await self._provider.generate_structured(
            [
                Message(role=Role.system, content=_CLASSIFY_SYSTEM + context_rules(context)),
                Message(
                    role=Role.user,
                    content=f"Question: {question}\n\nYour reply: {text}",
                ),
            ],
            QueryPlan,
        )
        if plan.kind == "refuse":
            return AgentTurn(kind="refuse", question=question, reason=plan.reason)
        return AgentTurn(
            kind="clarify",
            question=question,
            clarification=plan.clarification or text.strip(),
        )

    async def _plan(self, question: str, card: str, context: BusinessContext | None) -> QueryPlan:
        return await self._provider.generate_structured(
            [
                Message(role=Role.system, content=_SYSTEM + context_rules(context)),
                Message(role=Role.user, content=f"{card}\n\nQuestion: {question}"),
            ],
            QueryPlan,
        )

    async def _repair(
        self,
        question: str,
        card: str,
        previous: AnalyticalQuery,
        error: QueryValidationError,
        context: BusinessContext | None,
    ) -> QueryPlan:
        return await self._provider.generate_structured(
            [
                Message(role=Role.system, content=_SYSTEM + context_rules(context)),
                Message(
                    role=Role.user,
                    content=(
                        f"{card}\n\nQuestion: {question}\n\n"
                        "Your previous query was rejected:\n"
                        f"{previous.model_dump_json()}\n\n"
                        f"Reason: {error}\n\n"
                        "Return a corrected JSON object."
                    ),
                ),
            ],
            QueryPlan,
        )


def _stamp_timezone(resolved: ResolvedQuery, context: BusinessContext | None) -> ResolvedQuery:
    """A relative period needs a zone; use the source's when the query has none."""
    if resolved.time is None or resolved.time.timezone or context is None:
        return resolved
    return resolved.model_copy(
        update={"time": resolved.time.model_copy(update={"timezone": context.timezone})}
    )


def _summarize(result: QueryResult) -> str:
    """A short, factual headline — never a claim beyond the returned rows."""
    if not result.rows:
        return "No matching rows."
    if len(result.rows) == 1 and len(result.columns) == 1:
        # By name, not by position. A row carries more keys than `columns`
        # lists — Cube adds the time dimension and its granularity — so taking
        # the first value printed a date where the money should be. The one
        # number this whole loop exists to get right cannot be read positionally.
        row = result.rows[0]
        column = result.columns[0].name
        value = row.get(column, next(iter(row.values())))
        # A sum over nothing comes back as one NULL row, not zero rows. Printing
        # "None" reads like a failure; it means the period was empty.
        return "No matching rows." if value is None else str(value)
    suffix = "+" if result.truncated else ""
    return f"{result.row_count}{suffix} rows"


def explain(query: AnalyticalQuery, graph: SemanticGraph) -> str:
    """The "read from" trust line — built from the model, without the LLM."""
    by_id = {m.id: m for m in graph.metrics}
    by_norm = {normalise(m.name): m for m in graph.metrics}
    by_key = {e.key: e for e in graph.entities}

    def find(name: str) -> MetricDefinition | None:
        return by_id.get(name) or by_norm.get(normalise(name))

    chosen = [m for m in (find(name) for name in query.measures) if m is not None]
    segments = [_describe_metric(m) for m in chosen]
    text = "Read from: " + "; ".join(segments) if segments else "Read from: —"

    if query.time is not None:
        label = query.time.dimension.strip()
        if not label and chosen:
            label = _time_label(chosen[0], by_key) or "date"
        text += f", by {label} ({_period(query)})"

    if query.dimensions:
        text += f", sliced by {', '.join(query.dimensions)}"
    return text + "."


def _describe_metric(metric: MetricDefinition) -> str:
    if metric.kind == MetricKind.derived:
        return f"{metric.name} (= {metric.expression or '?'})"
    agg = metric.aggregation.value if metric.aggregation else "?"
    core = f"{agg} of {metric.column}" if metric.column else agg
    if metric.filters:
        conds = ", ".join(f"{f.field} {f.operator} {f.value}" for f in metric.filters)
        core += f" where {conds}"
    return f"{metric.name} ({core})"


def _time_label(metric: MetricDefinition, by_key: dict[str, Entity]) -> str:
    entity = by_key.get(metric.entity_key or "")
    if entity is None or not metric.time_dimension:
        return ""
    for dim in entity.dimensions:
        if dim.column == metric.time_dimension:
            return dim.name
    return ""


def _period(query: AnalyticalQuery) -> str:
    time = query.time
    if time is None:
        return "all time"
    if time.is_absolute:
        return f"{time.since}–{time.until}"
    if time.range:
        return time.range.replace("_", " ")
    return "all time"
