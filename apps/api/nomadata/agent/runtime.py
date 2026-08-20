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

_SYSTEM = (
    "You are a careful analytics assistant. Turn the user's question into ONE "
    "query against the semantic model given below.\n"
    "Use ONLY the exact metric and dimension names listed. Never invent a name, "
    "never write SQL, never reference a raw database column.\n"
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
        plan = await self._plan(question, card, context)

        if plan.kind == "clarify":
            return AgentTurn(
                kind="clarify",
                question=question,
                clarification=plan.clarification or "Could you rephrase that?",
            )
        if plan.kind == "refuse":
            return AgentTurn(
                kind="refuse",
                question=question,
                reason=plan.reason or "I can only answer questions about this data source.",
            )
        if plan.query is None:
            return AgentTurn(
                kind="clarify",
                question=question,
                clarification="I couldn't turn that into a query — can you be "
                "more specific about what to measure?",
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
        return str(next(iter(result.rows[0].values())))
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
