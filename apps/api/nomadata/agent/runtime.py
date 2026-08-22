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

import time
from collections.abc import Awaitable, Callable
from typing import Any

from nomadata.agent.catalog import model_card
from nomadata.agent.history import history_block
from nomadata.agent.resolver import QueryValidationError, ResolvedQuery, resolve
from nomadata.agent.tools import ToolBox, tool_specs
from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.models import (
    AgentStep,
    AgentTurn,
    AnalyticalQuery,
    BusinessContext,
    ConversationTurn,
    Entity,
    Message,
    MetricDefinition,
    MetricKind,
    QueryPlan,
    QueryResult,
    Role,
    SemanticGraph,
    TurnUsage,
)
from nomadata.logging import get_logger
from nomadata.query.cube import QueryEngineError
from nomadata.query.cube_schema import normalise
from nomadata.semantic.prompt import context_rules

log = get_logger()

_MAX_REPAIRS = 2

#: How many times the model may call tools before it has to have an answer.
#: Enough for look-up, correct a rejected name, run — and no room to wander.
_MAX_TOOL_TURNS = 4

#: Tool calls across the whole turn. The turn cap bounds the rounds, not the
#: work: one reply may carry any number of calls, and a model that asks for
#: forty spends forty queries against the user's database on one question.
_MAX_TOOL_CALLS = 12

#: How much of a tool's output travels with its step. Enough to read the rows
#: that were returned, not enough to store the whole result twice.
_MAX_STEP_DETAIL = 4000

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
    "You are a careful analytics assistant for one data source.\n"
    "LANGUAGE: write every word you say to the user in the language of their "
    "question. A question in Vietnamese is answered in Vietnamese, a question "
    "in English in English. This applies to every tool argument the user will "
    "read and to any plain reply. Metric and dimension names are identifiers — "
    "spell them exactly as the model does, never translate them.\n"
    "Every turn ends in exactly one of four ways, and three of them are tool "
    "calls:\n"
    "  - run_query — you know which metric answers the question. This is the "
    "goal; prefer it whenever the question is answerable.\n"
    "  - ask_back — the question is about this data but one thing has to be "
    "settled first. Name the candidates in the question you ask.\n"
    "  - decline — the question is not about this data, or asks to change data "
    "rather than read it. Deleting, updating and inserting are always decline.\n"
    "  - plain text with no tool call — ONLY for a greeting or a question about "
    "what you can do. Never use plain text to ask something or to turn "
    "something down: those have tools, and a turn that should have called one "
    "reaches the user unlabelled.\n"
    "The model card below lists the metrics and the columns of the tables they "
    "measure. It does NOT list the columns of joined tables, and it does not "
    "list every metric — call list_metrics when what you need is not there, "
    "instead of settling for something close.\n"
    "Use ONLY the exact metric and dimension names from the card or from a tool "
    "result. Never invent a name, never write SQL, never name a raw database "
    "column. A metric name is used on its own; a dimension may be written "
    '"Table.Name" when the same name appears on more than one table.\n'
    "If a tool rejects a name, read what it says and correct it.\n"
    "An empty result is not automatically a failure. run_query says which kind "
    "of empty it is: a filter matching nothing is worth fixing, a metric with "
    "no data at all is worth saying plainly, and a period with no rows IS the "
    "answer — report it. Never work through other periods or other metrics "
    "until some number appears.\n"
    "Before filtering on a dimension, call values_of to see what it holds. "
    "The model names the column, not its contents: a filter written as "
    "'Đã hoàn thành' against rows that say 'COMPLETED' returns nothing, and "
    "an empty result reads exactly like a real answer of zero.\n"
    "Before running anything, compare the user's words against the metric "
    "names. If one metric's name contains those words and the others do not, "
    "that is the metric — use it and do not ask. Call ask_back only when the "
    "words stop short of choosing: a bare 'doanh thu' or 'số tiền' where the "
    "model publishes several. Picking the likelier one produces a number that "
    "looks right and answers a question nobody asked, and nothing downstream "
    "can catch that."
)


_ENDING_LABEL = {"clarify": "Asking back", "refuse": "Declining", "reply": "Replying"}


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
        history: list[ConversationTurn] | None = None,
        on_step: StepSink | None = None,
    ) -> AgentTurn:
        started = time.monotonic()
        steps = _Steps(on_step)
        await steps.add("plan", "Reading the semantic model")
        card = model_card(graph, question=question)
        if self._provider.capabilities.tool_calling:
            turn = await self._answer_with_tools(
                question, card, graph, context, history or [], steps
            )
        else:
            turn = await self._answer_from_plan(question, card, graph, context)
        turn.steps = steps.taken

        turn.model_version = graph.version
        turn.usage.latency_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "agent.turn",
            kind=turn.kind,
            source_id=graph.source_id,
            latency_ms=turn.usage.latency_ms,
            llm_calls=turn.usage.llm_calls,
            tool_calls=turn.usage.tool_calls,
            tokens_in=turn.usage.tokens_in,
            tokens_out=turn.usage.tokens_out,
        )
        return turn

    async def _answer_with_tools(
        self,
        question: str,
        card: str,
        graph: SemanticGraph,
        context: BusinessContext | None,
        history: list[ConversationTurn],
        steps: _Steps,
    ) -> AgentTurn:
        """Let the model look things up, then run one query through the resolver.

        The card lists what the metrics measure; everything else — the 62 tables
        one join away and their 918 columns — is a tool call away instead of in
        every prompt. The loop ends the moment a query runs: the number and the
        "read from" line are built from the query that ran, not from the model
        describing what it did, so it has no chance to misreport either.
        """
        box = ToolBox(graph, self._engine)
        usage = TurnUsage()
        # Earlier turns as three lines each, not as a transcript. Replaying whole
        # turns grows with the conversation and hands back an old QueryResult the
        # model may read as current.
        earlier = history_block(history)
        parts = (
            [card, earlier, f"Question: {question}"] if earlier else [card, f"Question: {question}"]
        )
        messages = [
            Message(role=Role.system, content=_TOOL_SYSTEM + context_rules(context)),
            Message(role=Role.user, content="\n\n".join(parts)),
        ]

        for _ in range(_MAX_TOOL_TURNS):
            response = await self._provider.tool_call(messages, tool_specs())
            _add_usage(usage, response.usage)

            if not response.tool_calls:
                # It answered in prose despite being told not to. Keep the words
                # rather than discard them, but this is the channel where this
                # model falls apart — four languages in one paragraph — so it is
                # the fallback, not a supported ending.
                await steps.add("result", "Replying")
                return AgentTurn(
                    kind="reply",
                    question=question,
                    answer=(response.content or "").strip(),
                    usage=usage,
                )

            messages.append(
                Message(
                    role=Role.assistant,
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                if usage.tool_calls >= _MAX_TOOL_CALLS:
                    return AgentTurn(
                        kind="error",
                        question=question,
                        reason="I kept looking without getting to an answer.",
                        usage=usage,
                    )
                usage.tool_calls += 1
                step = await steps.add("tool", _tool_label(call.name, call.arguments))
                output = await box.run(call.name, call.arguments)
                # What the tool returned, beside what it was asked. Reading the
                # query without the rows it produced is half the account.
                await steps.finish(step, output[:_MAX_STEP_DETAIL])
                messages.append(
                    Message(role=Role.tool, content=output, tool_call_id=call.id, name=call.name)
                )
                if output.startswith("That did not work"):
                    # Worth showing: a rejected name is the agent correcting
                    # itself, and it explains why the turn took another round.
                    await steps.add("repair", "Correcting a rejected name", output[:200])

            if box.ended is not None:
                # A turn that tried to measure something and could not must not
                # end with the model saying a number anyway. It did: two failed
                # attempts, then `reply` with "là 0 VNĐ" — a figure no query
                # ever produced. The guarantee that a headline is computed
                # rather than narrated only ever covered the answer branch;
                # this is the branch it escaped through.
                if box.query_error is not None:
                    await steps.add("result", "Could not run the query")
                    return AgentTurn(
                        kind="error",
                        question=question,
                        reason=box.query_error,
                        usage=usage,
                    )

                # The model said which it was by choosing the tool, so there is
                # nothing left to classify — and no second LLM call to pay for.
                kind, text = box.ended
                await steps.add("result", _ENDING_LABEL.get(kind, "Replying"))
                return AgentTurn(
                    kind=kind,
                    question=question,
                    clarification=text if kind == "clarify" else "",
                    reason=text if kind == "refuse" else "",
                    answer=text if kind == "reply" else "",
                    usage=usage,
                )

            if box.last_result is not None and box.last_query is not None:
                await steps.add(
                    "result",
                    f"{box.last_result.row_count} "
                    f"{'row' if box.last_result.row_count == 1 else 'rows'}",
                )
                return AgentTurn(
                    kind="answer",
                    question=question,
                    query=box.last_query,
                    result=box.last_result,
                    answer=_summarize(box.last_result),
                    explanation=explain(box.last_query, graph),
                    # Why it was empty travels with the answer, not only back to
                    # the model. The reader is the one who decides whether a
                    # quiet month is right, and "no matching rows" on its own
                    # does not let them.
                    notes=[*box.last_notes, *_empty_note(box)],
                    usage=usage,
                )

        return AgentTurn(
            kind="error",
            question=question,
            reason="I looked, but couldn't turn that into a query I could run.",
            usage=usage,
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


#: Called as each step happens, so a caller can stream it while the turn runs.
StepSink = Callable[[AgentStep], Awaitable[None]]


class _Steps:
    """Collects the turn's steps and forwards each one as it happens.

    Both at once on purpose: the stream shows the work while it is happening,
    and the same list is stored with the turn, so reopening a thread shows how
    an answer was reached rather than only what it was.
    """

    def __init__(self, sink: StepSink | None) -> None:
        self.taken: list[AgentStep] = []
        self._sink = sink

    async def add(self, kind: str, label: str, detail: str = "") -> AgentStep:
        step = AgentStep(ordinal=len(self.taken) + 1, kind=kind, label=label, detail=detail)
        self.taken.append(step)
        if self._sink is not None:
            await self._sink(step)
        return step

    async def finish(self, step: AgentStep, detail: str) -> None:
        """Attach what a step produced, and send it again under the same id."""
        step.detail = detail
        if self._sink is not None:
            await self._sink(step)


def _tool_label(name: str, arguments: dict[str, Any]) -> str:
    """What a tool call is doing, in the words a person would use."""
    if name == "list_metrics":
        topic = str(arguments.get("topic") or "").strip()
        return f"Looking for metrics about “{topic}”" if topic else "Listing metrics"
    if name == "describe_metric":
        return f"Checking how “{arguments.get('name', '')}” is calculated"
    if name == "run_query":
        measures = arguments.get("measures") or []
        if isinstance(measures, list) and measures:
            return f"Running {', '.join(str(m) for m in measures)}"
        return "Running the query"
    return f"Calling {name}"


def _empty_note(box: ToolBox) -> list[str]:
    """The reason a result was empty, if there was one."""
    return [box.last_empty_note] if box.last_empty_note else []


def _add_usage(usage: TurnUsage, reported: dict[str, object]) -> None:
    """Fold one provider reply's token counts in. An agent turn is several calls.

    Gateways name these differently and sometimes report floats; anything that
    is not a plain number is skipped, because a usage figure is never worth
    failing a good answer over.
    """
    usage.llm_calls += 1
    for key in ("prompt_tokens", "input_tokens"):
        value = reported.get(key)
        if isinstance(value, int | float):
            usage.tokens_in += int(value)
            break
    for key in ("completion_tokens", "output_tokens"):
        value = reported.get(key)
        if isinstance(value, int | float):
            usage.tokens_out += int(value)
            break


def _merge(counted: TurnUsage, extra: TurnUsage) -> TurnUsage:
    return TurnUsage(
        tokens_in=counted.tokens_in + extra.tokens_in,
        tokens_out=counted.tokens_out + extra.tokens_out,
        llm_calls=counted.llm_calls + extra.llm_calls,
        tool_calls=counted.tool_calls + extra.tool_calls,
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

    # The filters the question added, not only the ones built into the metric.
    # Without this a filtered count and an unfiltered one produce the same
    # sentence and different numbers, and the line whose whole job is letting a
    # reader check the figure says nothing about the half that changed it.
    if query.filters:
        conditions = ", ".join(f"{f.field} {f.operator} {f.value}" for f in query.filters)
        text += f", where {conditions}"
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
