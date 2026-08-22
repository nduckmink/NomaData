"""Turn a sentence into a definition — "add/edit by prompt", where you are.

This is the fastest path a user has to a correct metric: they type
*"tổng học phí đã thu, tính theo ngày thanh toán"* and the editor form comes
back filled in. Two properties make it safe:

- **Nothing is saved.** The result populates the form; the human presses Save.
- **Nothing is trusted.** Every field the model returns is checked against the
  real catalog — a column that isn't on the entity, an aggregation that doesn't
  fit the type, a filter on a column that doesn't exist. A field that fails its
  check is left *blank with a warning*, never quietly filled with a guess.

Scope is one metric, so the prompt stays small and the blast radius of a bad
answer is a single form the user is looking straight at.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from nomadata.core.formula import is_closed, referenced_metrics
from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.models import (
    FILTER_OPERATORS,
    VALUELESS_OPERATORS,
    Aggregation,
    BusinessContext,
    Dimension,
    DimensionKind,
    Entity,
    EntityDraftRequest,
    EntityDraftResponse,
    EntityProposal,
    Filter,
    Message,
    MetricDefinition,
    MetricDraftRequest,
    MetricDraftResponse,
    MetricKind,
    MetricProposal,
    MetricProposalList,
    MetricSuggestRequest,
    MetricSuggestResponse,
    Origin,
    Provenance,
    Role,
    SemanticGraph,
)
from nomadata.logging import get_logger
from nomadata.semantic.prompt import context_rules

log = get_logger()

_FORMATS = {"number", "currency", "percent"}
_NUMERIC_ONLY = {Aggregation.sum, Aggregation.avg}
_NEEDS_COLUMN = {
    Aggregation.count_distinct,
    Aggregation.sum,
    Aggregation.avg,
    Aggregation.min,
    Aggregation.max,
}
#: How many entities to describe in full when the user hasn't picked one. The
#: prompt must stay small on a 124-table database, so candidates are ranked
#: lexically first — a deterministic filter, not a second AI call.
_MAX_CANDIDATE_ENTITIES = 8
_MAX_COLUMNS_PER_ENTITY = 25

_WORD = re.compile(r"[A-Za-zÀ-ỹ0-9_]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text or "") if len(t) > 1}


def rank_entities(graph: SemanticGraph, prompt: str, limit: int) -> list[Entity]:
    """Entities most likely to be what the prompt is about.

    Pure lexical overlap between the prompt and each entity's name, table and
    column names. Crude on purpose: it only has to get the right entity into a
    shortlist of eight, and the model still chooses from it.
    """
    wanted = _tokens(prompt)
    if not wanted:
        return list(graph.entities)[:limit]

    scored: list[tuple[int, Entity]] = []
    for entity in graph.entities:
        haystack = _tokens(f"{entity.name} {entity.table} {entity.description or ''}")
        for dim in entity.dimensions:
            haystack |= _tokens(f"{dim.name} {dim.column}")
        scored.append((len(wanted & haystack), entity))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entity for _, entity in scored[:limit]]


def _describe(entity: Entity) -> dict[str, Any]:
    columns: list[dict[str, Any]] = [
        {
            "column": dim.column,
            "type": str(dim.kind),
            "name": dim.name,
            # Real values are what let the model map "đã thu" onto 'DA_THU'
            # instead of inventing a plausible-looking constant.
            "values": dim.sample_values[:8] or None,
        }
        for dim in entity.dimensions
        if not dim.hidden
    ][:_MAX_COLUMNS_PER_ENTITY]
    return {
        "entity_key": entity.key,
        "name": entity.name,
        "table": entity.table,
        "description": entity.description,
        "columns": columns,
    }


_SYSTEM = (
    "You define ONE metric for a business intelligence semantic model.\n"
    "A metric is either:\n"
    "  base    — an aggregation (count, count_distinct, sum, avg, min, max) over "
    "one column of one entity, with optional filters and a time column;\n"
    "  derived — an arithmetic expression over other metrics, by name.\n"
    "Rules you must follow:\n"
    "  - Use ONLY the entity_key, column names and filter values given below. "
    "Never invent a table, a column or a value.\n"
    "  - sum and avg require a numeric column. count needs no column.\n"
    "  - time_dimension must be a column whose type is 'time'.\n"
    "  - filter operator must be one of: eq, neq, gt, gte, lt, lte, in, not_in, "
    "contains, set, not_set.\n"
    "  - If the request is ambiguous, choose the most conventional reading and "
    "explain it in `reasoning`.\n"
    "  - When editing an existing metric, change ONLY what the instruction asks "
    "for and repeat every other field unchanged.\n"
    "Return ONLY a JSON object."
)


class MetricDrafter:
    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def draft(
        self,
        request: MetricDraftRequest,
        graph: SemanticGraph,
        *,
        context: BusinessContext | None = None,
    ) -> MetricDraftResponse:
        candidates = self._candidates(request, graph)
        if not candidates:
            raise ValueError("This model has no entities to build a metric from.")

        proposal = await self._provider.generate_structured(
            [
                Message(role=Role.system, content=_SYSTEM + context_rules(context)),
                Message(role=Role.user, content=self._user_message(request, graph, candidates)),
            ],
            MetricProposal,
        )
        return self.materialize(proposal, request, graph, candidates)

    # ------------------------------------------------------------------

    def _candidates(self, request: MetricDraftRequest, graph: SemanticGraph) -> list[Entity]:
        pinned = request.entity_key or (request.base.entity_key if request.base else None)
        if pinned:
            chosen = [e for e in graph.entities if e.key == pinned]
            if chosen:
                return chosen
        return rank_entities(graph, request.prompt, _MAX_CANDIDATE_ENTITIES)

    def _user_message(
        self, request: MetricDraftRequest, graph: SemanticGraph, candidates: list[Entity]
    ) -> str:
        blocks = [
            "Entities you may use:",
            json.dumps([_describe(e) for e in candidates], ensure_ascii=False, default=str),
        ]
        names = [m.name for m in graph.metrics if m.name.strip()]
        if names:
            blocks.append(
                "Existing metric names (only these may appear in a derived "
                f"expression): {json.dumps(names[:60], ensure_ascii=False)}"
            )
        if request.base is not None:
            blocks.append(
                "Edit this existing metric:\n"
                + request.base.model_dump_json(
                    indent=2,
                    include={
                        "name",
                        "description",
                        "kind",
                        "entity_key",
                        "aggregation",
                        "column",
                        "filters",
                        "time_dimension",
                        "expression",
                        "format",
                    },
                )
            )
            blocks.append(f"Change requested: {request.prompt}")
        else:
            blocks.append(f"Metric to define: {request.prompt}")
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------
    # Validation — the model proposes, the catalog decides
    #
    # ``materialize`` is public because bulk suggestion runs every proposal
    # through the same checks a single hand-written one gets. One checker, one
    # set of rules.
    # ------------------------------------------------------------------

    def materialize(
        self,
        proposal: MetricProposal,
        request: MetricDraftRequest,
        graph: SemanticGraph,
        candidates: list[Entity],
    ) -> MetricDraftResponse:
        base = request.base
        warnings: list[str] = []

        entity = self._resolve_entity(proposal, graph, candidates, base, warnings)
        kind = MetricKind.derived if proposal.kind.strip().lower() == "derived" else MetricKind.base

        metric = MetricDefinition(
            id=base.id if base is not None else uuid4().hex,
            name=proposal.name.strip() or (base.name if base else ""),
            description=proposal.description.strip() or (base.description if base else None),
            kind=kind,
            provenance=Provenance(origin=Origin.ai),
        )

        if kind == MetricKind.derived:
            metric = metric.model_copy(
                update={
                    "expression": proposal.expression.strip() or (base.expression if base else None)
                }
            )
            self._warn_unknown_metrics(metric.expression, graph, warnings)
        else:
            dims = {d.column: d for d in entity.dimensions} if entity else {}
            metric = metric.model_copy(
                update={
                    "entity_key": entity.key if entity else None,
                    "aggregation": self._aggregation(proposal, base, warnings),
                }
            )
            metric = metric.model_copy(
                update={
                    "column": self._column(proposal, metric.aggregation, dims, entity, warnings),
                    "time_dimension": self._time_dimension(proposal, dims, warnings),
                    "filters": self._filters(proposal, dims, warnings),
                }
            )

        fmt = proposal.format.strip().lower()
        metric = metric.model_copy(
            update={"format": fmt if fmt in _FORMATS else (base.format if base else None)}
        )

        return MetricDraftResponse(
            metric=metric,
            changed_fields=_changed_fields(base, metric),
            reasoning=proposal.reasoning.strip(),
            warnings=warnings,
        )

    def _resolve_entity(
        self,
        proposal: MetricProposal,
        graph: SemanticGraph,
        candidates: list[Entity],
        base: MetricDefinition | None,
        warnings: list[str],
    ) -> Entity | None:
        by_key = {e.key: e for e in graph.entities}
        proposed = proposal.entity_key.strip()
        if proposed in by_key:
            return by_key[proposed]
        if proposed:
            # Models often echo the display name or the table instead of the key.
            for entity in graph.entities:
                if proposed in (entity.name, entity.table):
                    return entity
            warnings.append(f"AI referred to an unknown entity {proposed!r}; kept the current one.")
        if base is not None and base.entity_key in by_key:
            return by_key[base.entity_key]
        return candidates[0] if candidates else None

    def _aggregation(
        self,
        proposal: MetricProposal,
        base: MetricDefinition | None,
        warnings: list[str],
    ) -> Aggregation | None:
        raw = proposal.aggregation.strip().lower().replace(" ", "_")
        if not raw:
            return base.aggregation if base else None
        try:
            return Aggregation(raw)
        except ValueError:
            warnings.append(f"Unknown aggregation {proposal.aggregation!r} — left blank.")
            return None

    def _column(
        self,
        proposal: MetricProposal,
        aggregation: Aggregation | None,
        dims: dict[str, Dimension],
        entity: Entity | None,
        warnings: list[str],
    ) -> str | None:
        if aggregation is not None and aggregation not in _NEEDS_COLUMN:
            return None  # count needs no column
        column = proposal.column.strip()
        if not column:
            if aggregation in _NEEDS_COLUMN:
                warnings.append(f"{aggregation} needs a column, but none was proposed.")
            return None
        known = column in dims or (entity is not None and column == entity.primary_key)
        if not known:
            warnings.append(
                f"Column {column!r} is not on "
                f"{entity.name if entity else 'this entity'} — left blank."
            )
            return None
        dim = dims.get(column)
        if aggregation in _NUMERIC_ONLY and dim is not None and dim.kind != DimensionKind.number:
            warnings.append(
                f"{aggregation} needs a numeric column, but {column!r} is {dim.kind} — left blank."
            )
            return None
        return column

    def _time_dimension(
        self, proposal: MetricProposal, dims: dict[str, Dimension], warnings: list[str]
    ) -> str | None:
        column = proposal.time_dimension.strip()
        if not column:
            return None
        dim = dims.get(column)
        if dim is None:
            warnings.append(f"Time column {column!r} is not on this entity — left blank.")
            return None
        if dim.kind != DimensionKind.time:
            warnings.append(f"{column!r} is not a date/time column — left blank.")
            return None
        return column

    def _filters(
        self, proposal: MetricProposal, dims: dict[str, Dimension], warnings: list[str]
    ) -> list[Filter]:
        filters: list[Filter] = []
        for raw in proposal.filters:
            field = raw.field.strip()
            if not field:
                continue
            if field not in dims:
                warnings.append(f"Filter on unknown column {field!r} was dropped.")
                continue
            operator = raw.operator.strip().lower() or "eq"
            if operator not in FILTER_OPERATORS:
                warnings.append(f"Unknown filter operator {raw.operator!r} on {field!r} — dropped.")
                continue
            value = None if operator in VALUELESS_OPERATORS else raw.value
            if operator not in VALUELESS_OPERATORS and value in (None, ""):
                warnings.append(f"Filter on {field!r} had no value — dropped.")
                continue
            filters.append(Filter(field=field, operator=operator, value=value))

            samples = dims[field].sample_values
            if operator == "eq" and samples and str(value) not in {str(s) for s in samples}:
                warnings.append(
                    f"{value!r} was not among the sampled values of {field!r} — "
                    "check it before saving."
                )
        return filters

    def _warn_unknown_metrics(
        self, expression: str | None, graph: SemanticGraph, warnings: list[str]
    ) -> None:
        if not expression:
            warnings.append("No expression was produced for this derived metric.")
            return
        names = {m.name.strip() for m in graph.metrics if m.name.strip()}
        for token in re.findall(r"[A-Za-zÀ-ỹ_][A-Za-zÀ-ỹ0-9_ ]*", expression):
            token = token.strip()
            if token and token not in names:
                warnings.append(f"{token!r} in the expression is not an existing metric.")


def _changed_fields(base: MetricDefinition | None, metric: MetricDefinition) -> list[str]:
    """Which form fields the client should highlight."""
    tracked = (
        "name",
        "description",
        "kind",
        "entity_key",
        "aggregation",
        "column",
        "time_dimension",
        "expression",
        "format",
        "filters",
    )
    if base is None:
        return [f for f in tracked if _present(getattr(metric, f))]
    return [f for f in tracked if getattr(base, f) != getattr(metric, f)]


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


_ENTITY_SYSTEM = (
    "You name and describe ONE entity in a business intelligence semantic "
    "model. An entity is a database table treated as a business object.\n"
    "Rules you must follow:\n"
    "  - Write a short business Name (a noun phrase, not a sentence) and a "
    "one-sentence Description of what this table holds and what it is used "
    "for.\n"
    "  - Base both on the columns you are given. Never claim the table contains "
    "something its columns do not support.\n"
    "  - You may NOT change the table, its columns or its key — only the "
    "wording.\n"
    "Return ONLY a JSON object."
)


class EntityDrafter:
    """Name and describe a single entity from a sentence.

    Deliberately text-only: an entity's structure comes from the database, so
    the model has nothing structural to get wrong here. That makes this the
    cheapest, safest use of the prompt box — and it replaces a whole-graph
    re-enrichment pass that rewrote things the user had not asked about.
    """

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def draft(
        self,
        request: EntityDraftRequest,
        graph: SemanticGraph,
        *,
        context: BusinessContext | None = None,
    ) -> EntityDraftResponse:
        entity = next((e for e in graph.entities if e.key == request.entity_key), None)
        if entity is None:
            raise ValueError(f"No entity {request.entity_key!r} in this model.")

        payload = {
            "table": entity.table,
            "current_name": entity.name,
            "current_description": entity.description,
            "columns": [
                {
                    "column": d.column,
                    "type": str(d.kind),
                    "values": d.sample_values[:6] or None,
                }
                for d in entity.dimensions
                if not d.hidden
            ][:_MAX_COLUMNS_PER_ENTITY],
            "metrics_measured_here": [m.name for m in graph.metrics if m.entity_key == entity.key][
                :20
            ],
        }

        proposal = await self._provider.generate_structured(
            [
                Message(role=Role.system, content=_ENTITY_SYSTEM + context_rules(context)),
                Message(
                    role=Role.user,
                    content=(
                        "Entity:\n"
                        + json.dumps(payload, ensure_ascii=False, default=str)
                        + f"\n\nWhat the user says about it: {request.prompt}"
                    ),
                ),
            ],
            EntityProposal,
        )

        # Blank text is not an answer: keep what is there rather than wiping a
        # name because the model returned an empty string.
        warnings: list[str] = []
        name = proposal.name.strip()
        if not name:
            name = entity.name
            warnings.append("The model returned no name; kept the current one.")
        description = proposal.description.strip()
        if not description:
            description = entity.description or ""
            warnings.append("The model returned no description; kept the current one.")

        changed = [
            field
            for field, before, after in (
                ("name", entity.name, name),
                ("description", entity.description or "", description),
            )
            if before != after
        ]
        return EntityDraftResponse(
            name=name,
            description=description,
            changed_fields=changed,
            reasoning=proposal.reasoning.strip(),
            warnings=warnings,
        )


_SUGGEST_SYSTEM = (
    "You propose the metrics a business would actually track on ONE entity of "
    "a semantic model.\n"
    "Rules you must follow:\n"
    "  - Propose only metrics that are worth putting on a dashboard. Most "
    "numeric columns are identifiers, foreign keys, codes or flags: summing "
    "them is meaningless. Skip those.\n"
    "  - Use ONLY the columns given. Never invent a column or a value.\n"
    "  - sum and avg require a numeric column. count needs no column.\n"
    "  - time_dimension must be a column whose type is 'time'. Prefer the date "
    "the event actually happened over a record-created date.\n"
    "  - Filters must use the sample values shown for that column.\n"
    "  - Propose FEWER good metrics rather than padding the list.\n"
    "  - Every proposal needs a short business name and a one-sentence "
    "description, plus `reasoning` saying why it is worth tracking.\n"
    "Return ONLY a JSON object."
)


def _derived_problem(metric: MetricDefinition, entity_key: str, graph: SemanticGraph) -> str | None:
    """Why this formula cannot ship, or ``None`` if it can.

    Cube builds a calculated measure inside one cube, so a formula naming a
    metric from another table compiles to nothing: it lands in the model, the
    query layer never sees it, and nobody is told. That makes an unchecked
    derived proposal worse than a rejected one — it looks like a metric the
    whole way to the person asking a question it cannot answer.
    """
    expression = (metric.expression or "").strip()
    if not expression:
        return "it has no formula"

    known = {
        m.name
        for m in graph.metrics
        if m.entity_key == entity_key and m.kind == MetricKind.base and m.name.strip()
    }
    # Closure first: a formula with one real name and one invented one fails
    # both checks, and "names something this table does not have" is the half
    # that tells the reader what to fix.
    if not is_closed(expression, known):
        return "its formula names something this table does not have"
    if len(referenced_metrics(expression, known)) < 2:
        return "its formula combines fewer than two metrics of this table"
    return None


_DERIVE_SYSTEM = (
    "You propose RATIO metrics: numbers made by dividing or combining metrics "
    "that already exist on one entity.\n"
    "This is the half of a semantic model that base metrics cannot express, and "
    "the half a business steers by. A total says how much happened; a ratio says "
    "whether it is going well — recovery rate, fee as a share of volume, average "
    "value per transaction, approval rate.\n"
    "Rules you must follow:\n"
    "  - kind is always 'derived', and `expression` uses ONLY the metric names "
    "listed, joined by + - * / and parentheses. No columns, no invented names, "
    "no numbers of your own beyond a factor like 100 for a percentage.\n"
    "  - Spell every name in the expression exactly as it is listed.\n"
    "  - Combine only comparable things. Money over a row count is an average "
    "and is useful; money over a sum of days is nothing.\n"
    "  - Set `format` to 'percent' for a share, and leave it empty otherwise.\n"
    "  - Two or three good ones beat a list. If nothing here divides "
    "meaningfully, return an empty list.\n"
    "  - Each needs a business name, a one-sentence description, and "
    "`reasoning` saying what decision it informs.\n"
    "Return ONLY a JSON object."
)


class MetricSuggester:
    """Propose metrics for one entity.

    The heuristic build deliberately creates only a row count per entity, because
    auto-summing every numeric column yields ``SUM(bank_id)`` and friends. Which
    columns are worth measuring is a judgement call that needs the column names,
    their types and their real values — which is exactly what a model can read
    and a rule cannot. Every proposal is then put through the same catalog check
    as a hand-written one, so a bad idea comes back visibly incomplete rather
    than silently wrong.
    """

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider
        self._drafter = MetricDrafter(provider)

    async def suggest_derived(
        self,
        entity_key: str,
        graph: SemanticGraph,
        *,
        context: BusinessContext | None = None,
        limit: int = 3,
    ) -> MetricSuggestResponse:
        """Propose ratios over the base metrics an entity already has.

        A pass of its own because it can only run once base metrics exist: when
        the first pass looks at a table all it has is a row count, and there is
        nothing to divide. Scoped to one entity because Cube builds a calculated
        measure inside a single cube — a formula whose parts sit on two tables
        compiles to nothing at all and is dropped without a word.
        """
        entity = next((e for e in graph.entities if e.key == entity_key), None)
        if entity is None:
            raise ValueError(f"No entity {entity_key!r} in this model.")

        base = [
            m
            for m in graph.metrics
            if m.entity_key == entity_key and m.kind == MetricKind.base and m.name.strip()
        ]
        if len(base) < 2:
            return MetricSuggestResponse(metrics=[], reasons=[], warnings=[])

        existing = [m for m in graph.metrics if m.entity_key == entity_key]
        available = json.dumps(
            [{"name": m.name, "means": m.description or ""} for m in base],
            ensure_ascii=False,
        )
        already = json.dumps([_recipe(m) for m in existing], ensure_ascii=False, default=str)
        proposals = await self._provider.generate_structured(
            [
                Message(role=Role.system, content=_DERIVE_SYSTEM + context_rules(context)),
                Message(
                    role=Role.user,
                    content="\n\n".join(
                        [
                            f"Entity: {entity.name}",
                            "Metrics available to combine (use these names exactly):\n" + available,
                            "Already defined, do not repeat: " + already,
                            f"Propose at most {limit} ratio metrics.",
                        ]
                    ),
                ),
            ],
            MetricProposalList,
        )

        metrics: list[MetricDefinition] = []
        reasons: list[str] = []
        warnings: list[str] = []
        seen = {_recipe(m) for m in existing}

        for raw in proposals.metrics[:limit]:
            proposal = raw.model_copy(update={"kind": "derived", "entity_key": entity_key})
            drafted = self._drafter.materialize(
                proposal,
                MetricDraftRequest(prompt="", entity_key=entity_key),
                graph,
                [entity],
            )
            metric = drafted.metric
            expression = (metric.expression or "").strip()
            if not metric.name.strip() or not expression:
                warnings.append("Skipped a ratio with no name or no expression.")
                continue
            problem = _derived_problem(metric, entity_key, graph)
            if problem is not None:
                warnings.append(f"Skipped {metric.name!r}: {problem}.")
                continue
            recipe = _recipe(metric)
            if recipe in seen:
                continue
            seen.add(recipe)
            metrics.append(metric)
            reasons.append(drafted.reasoning or raw.reasoning.strip())

        return MetricSuggestResponse(metrics=metrics, reasons=reasons, warnings=warnings)

    async def suggest(
        self,
        request: MetricSuggestRequest,
        graph: SemanticGraph,
        *,
        context: BusinessContext | None = None,
    ) -> MetricSuggestResponse:
        entity = next((e for e in graph.entities if e.key == request.entity_key), None)
        if entity is None:
            raise ValueError(f"No entity {request.entity_key!r} in this model.")

        existing = [m for m in graph.metrics if m.entity_key == entity.key]
        proposals = await self._provider.generate_structured(
            [
                Message(role=Role.system, content=_SUGGEST_SYSTEM + context_rules(context)),
                Message(
                    role=Role.user,
                    content=(
                        "Entity:\n"
                        + json.dumps(_describe(entity), ensure_ascii=False, default=str)
                        + "\n\nMetrics it already has (do not repeat them): "
                        + json.dumps(
                            [_recipe(m) for m in existing], ensure_ascii=False, default=str
                        )
                        + f"\n\nPropose at most {request.limit} metrics."
                    ),
                ),
            ],
            MetricProposalList,
        )

        metrics: list[MetricDefinition] = []
        reasons: list[str] = []
        warnings: list[str] = []
        seen = {_recipe(m) for m in existing}

        for proposal in proposals.metrics[: request.limit]:
            drafted = self._drafter.materialize(
                proposal,
                MetricDraftRequest(prompt="", entity_key=entity.key),
                graph,
                [entity],
            )
            metric = drafted.metric
            if not metric.name.strip():
                warnings.append("Skipped a proposal with no name.")
                continue
            # A proposal that failed its own checks would land in the form blank;
            # for a bulk suggestion that is noise, so it is dropped and reported.
            if metric.kind == MetricKind.base and (
                metric.aggregation is None
                or (metric.aggregation in _NEEDS_COLUMN and not metric.column)
            ):
                warnings.append(f"Skipped {metric.name!r}: {'; '.join(drafted.warnings)}")
                continue
            # This pass is asked for metrics over an entity's *columns*, but
            # nothing stops a model answering with a formula, and until now
            # nothing checked one either — every guard here tested `kind ==
            # base`. That is how a ratio spanning two tables reached a published
            # model, where it compiles to nothing and is quietly absent.
            if metric.kind == MetricKind.derived:
                problem = _derived_problem(metric, entity.key, graph)
                if problem is not None:
                    warnings.append(f"Skipped {metric.name!r}: {problem}.")
                    continue
            recipe = _recipe(metric)
            if recipe in seen:
                continue  # the model repeated something already on the entity
            seen.add(recipe)
            metrics.append(metric)
            reasons.append(drafted.reasoning or proposal.reasoning.strip())

        return MetricSuggestResponse(metrics=metrics, reasons=reasons, warnings=warnings)


def _recipe(metric: MetricDefinition) -> str:
    """What a metric measures, ignoring what it is called — used to spot a
    proposal that duplicates one the entity already has under another name."""
    if metric.kind == MetricKind.derived:
        return f"={(metric.expression or '').strip()}"
    filters = sorted(f"{f.field}{f.operator}{f.value}" for f in metric.filters)
    return f"{metric.aggregation}({metric.column or '*'})[{','.join(filters)}]"
