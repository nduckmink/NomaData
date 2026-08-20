"""Check a query before anything runs it.

This is the query-time twin of ``semantic/validator.py``, which stops a broken
model from being published. The same reasoning applies one layer down: a query
that names something the model does not have should fail *here*, with a sentence
a person can act on, rather than at Cube as ``Member not found``.

Three jobs, in order:

1. **Translate.** Callers speak business names ("Học phí đã thu"); Cube runs
   members (``Scp_hoc_phi.Hoc_phi_da_thu``). The mapping comes from
   ``cube_schema.member_map`` — the module that invents those identifiers — so
   the two can never drift apart.
2. **Fill in what the model already knows.** A metric declares the date it is
   measured over. Making the caller choose that again each time re-opens the
   commonest way a BI number goes quietly wrong.
3. **Refuse clearly.** An unknown name comes back with the closest real one,
   because "did you mean X" turns a dead end into a next step.

Pure: no database, no network. The agent uses it, and so does ``/query`` for a
hand-written query.
"""

from __future__ import annotations

import difflib

from nomadata.core.errors import NomaDataError
from nomadata.core.models import (
    AnalyticalQuery,
    MetricDefinition,
    SemanticGraph,
    TimeSpec,
)
from nomadata.query.cube_schema import MemberMap, member_map, normalise

#: How close a name has to be before suggesting it is more help than noise.
_SUGGESTION_CUTOFF = 0.5


class QueryValidationError(NomaDataError):
    """The query names something the published model does not have.

    Carries the offending value and the nearest real one, so a person and the
    agent's repair turn are both told what to do, not only what is wrong.
    """

    def __init__(
        self, message: str, *, field: str = "", value: str = "", did_you_mean: str = ""
    ) -> None:
        super().__init__(message)
        self.field = field
        self.value = value
        self.did_you_mean = did_you_mean


class ResolvedQuery(AnalyticalQuery):
    """A query whose every name is a member Cube can run.

    A separate type so the signature says which side of the resolver a value
    came from: an ``AnalyticalQuery`` holds business names, a ``ResolvedQuery``
    holds members and is safe to execute.
    """

    #: Business-language remarks for the answer's "read from" line — e.g. that
    #: the metric is normally measured by a different date than the one asked.
    notes: list[str] = []


def resolve(query: AnalyticalQuery, graph: SemanticGraph) -> ResolvedQuery:
    """Turn business names into Cube members, or explain why it cannot."""
    mapping = member_map(graph)
    by_id = {m.id: m for m in graph.metrics}
    notes: list[str] = []

    if not query.measures:
        raise QueryValidationError(
            "A query has to measure something — no metric was named.",
            field="measures",
        )

    chosen: list[MetricDefinition] = []
    measures: list[str] = []
    for name in query.measures:
        metric_id = mapping.metric_id(name)
        if metric_id is None:
            _fail(
                f"No metric called {name!r} in this model.",
                field="measures",
                value=name,
                candidates=list(mapping.measure_labels.values()),
            )
            raise AssertionError("unreachable")  # pragma: no cover
        chosen.append(by_id[metric_id])
        measures.append(mapping.measures[metric_id])

    return ResolvedQuery(
        measures=measures,
        dimensions=[_dimension(name, mapping) for name in query.dimensions],
        filters=[
            f.model_copy(update={"field": _dimension(f.field, mapping)}) for f in query.filters
        ],
        time=_time(query.time, chosen, mapping, notes),
        limit=query.limit,
        order_by=[_order(o, mapping) for o in query.order_by],
        notes=notes,
    )


def _time(
    spec: TimeSpec | None,
    metrics: list[MetricDefinition],
    mapping: MemberMap,
    notes: list[str],
) -> TimeSpec | None:
    """Resolve the time axis, defaulting to the one the metrics declare.

    A metric records which date it is measured over — a decision a human made
    and published. Re-deciding it per question is how a total ends up counted by
    ``created_at`` instead of ``paid_at``: the figure looks entirely normal and
    is wrong, which is the commonest modelling error there is.
    """
    declared = {m.time_dimension for m in metrics if m.time_dimension}
    if len(declared) > 1:
        # Two metrics on different time axes cannot share one time filter — the
        # resulting table would mix periods without saying so.
        names = ", ".join(sorted(declared))
        raise QueryValidationError(
            f"These metrics are measured over different dates ({names}), so they "
            "cannot share one time axis. Ask for them separately.",
            field="time",
        )

    default = next(iter(declared), "")
    if spec is None:
        return None

    wanted = spec.dimension.strip() or default
    if not wanted:
        raise QueryValidationError(
            "This query filters by time, but no date column was given and the "
            "metric does not declare one.",
            field="time.dimension",
        )

    member = mapping.time_dimensions.get(normalise(wanted))
    if member is None:
        _fail(
            f"{wanted!r} is not a date column in this model.",
            field="time.dimension",
            value=wanted,
            candidates=_labels(mapping.time_dimensions),
        )
        raise AssertionError("unreachable")  # pragma: no cover

    # Measuring by another date is allowed — but the answer has to say so, since
    # the number itself gives no hint that it happened.
    if default and mapping.time_dimensions.get(normalise(default)) != member:
        notes.append(f"Measured by {wanted}, though this metric is normally measured by {default}.")
    return spec.model_copy(update={"dimension": member})


def _dimension(name: str, mapping: MemberMap) -> str:
    member = mapping.dimensions.get(normalise(name))
    if member is None:
        _fail(
            f"No dimension called {name!r} in this model.",
            field="dimensions",
            value=name,
            candidates=_labels(mapping.dimensions),
        )
        raise AssertionError("unreachable")  # pragma: no cover
    return member


def _order(term: str, mapping: MemberMap) -> str:
    """Ordering names a measure or a dimension, optionally prefixed with ``-``."""
    descending = term.startswith("-")
    name = term.lstrip("-")
    member = mapping.measure(name) or mapping.dimensions.get(normalise(name))
    if member is None:
        _fail(
            f"Cannot order by {name!r} — it is neither a metric nor a dimension here.",
            field="order_by",
            value=name,
            candidates=[*mapping.measure_labels.values(), *_labels(mapping.dimensions)],
        )
        raise AssertionError("unreachable")  # pragma: no cover
    return f"-{member}" if descending else member


def _labels(members: dict[str, str]) -> list[str]:
    """Readable names worth suggesting, one per member rather than every alias."""
    return sorted({member.split(".", 1)[-1] for member in members.values()})


def _suggest(value: str, candidates: list[str]) -> str:
    wanted = normalise(value)
    matches = difflib.get_close_matches(
        wanted, [normalise(c) for c in candidates], n=1, cutoff=_SUGGESTION_CUTOFF
    )
    if not matches:
        return ""
    return next((c for c in candidates if normalise(c) == matches[0]), "")


def _fail(message: str, *, field: str, value: str, candidates: list[str]) -> None:
    """Raise with the nearest real name attached. Always raises."""
    suggestion = _suggest(value, candidates)
    if suggestion:
        message = f"{message} Did you mean {suggestion!r}?"
    raise QueryValidationError(message, field=field, value=value, did_you_mean=suggestion)


def queryable_metrics(graph: SemanticGraph) -> list[MetricDefinition]:
    """Metrics the query layer can actually run.

    A derived metric whose parts live on different entities cannot become a Cube
    calculated measure, so it is left out: a name a caller may pick but nothing
    can execute is worse than one that was never offered.
    """
    runnable = set(member_map(graph).measures)
    return [m for m in graph.metrics if m.id in runnable]
