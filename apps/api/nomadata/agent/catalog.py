"""The model card — a compact, published-only view of the semantic model.

The agent is never handed the whole ``SemanticGraph``: a 15-table model is tens
of thousands of tokens and carries sample values it does not need to *plan* a
query. This builds the short catalogue the model reasons over — only what was
published (never hidden, never a draft), written in the business names a human
reviewed. When there are more metrics than fit, they are ranked against the
question and the card says it was trimmed, rather than dropping some silently.
"""

from __future__ import annotations

import re
import unicodedata

from nomadata.agent.resolver import queryable_metrics
from nomadata.core.models import Entity, MetricDefinition, SemanticGraph

_WORD = re.compile(r"[A-Za-z0-9_]+")
_MAX_DIMS_PER_ENTITY = 24

#: `đ` is not an accented `d`, so decomposition cannot reach it and it would be
#: dropped outright — the same substitution the Cube identifier folder makes.
_STANDALONE_LETTERS = str.maketrans({"đ": "d", "Đ": "D"})


def _tokens(text: str) -> set[str]:
    """Comparable words, with Vietnamese accents folded away.

    People type Vietnamese without diacritics constantly — "tong so tien ung
    luong" for "Tổng số tiền ứng lương". Matching those as different words made
    every accent-free question score zero against every metric, so the card fell
    back to graph order: 122 auto-generated Count metrics, and not one of the
    real ones. The agent then truthfully reported that the model had nothing but
    counts.
    """
    folded = unicodedata.normalize("NFKD", (text or "").translate(_STANDALONE_LETTERS))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    return {t.lower() for t in _WORD.findall(ascii_text) if len(t) > 1}


def _rank(metric: MetricDefinition, wanted: set[str]) -> tuple[int, int]:
    """How much of the card a metric deserves: relevance first, substance second.

    The build gives every table a `<Entity> Count`, so a large model is mostly
    counts — 122 of them here against 16 metrics a person or the AI actually
    designed. On a question that matches nothing in particular they all tie at
    zero relevance, and the counts win purely by being first in the graph. The
    second key breaks that tie towards the metrics someone meant.
    """
    relevance = len(wanted & _tokens(f"{metric.name} {metric.description or ''}"))
    designed = 0 if _is_plain_count(metric) else 1
    return relevance, designed


def _is_plain_count(metric: MetricDefinition) -> bool:
    """An unfiltered row count — what the heuristic build makes for every table."""
    return (
        metric.aggregation is not None
        and metric.aggregation.value == "count"
        and not metric.filters
        and not metric.column
    )


def _time_label(metric: MetricDefinition, by_key: dict[str, Entity]) -> str:
    """Display name of the date a metric is measured over, if any."""
    entity = by_key.get(metric.entity_key or "")
    if entity is None or not metric.time_dimension:
        return ""
    for dim in entity.dimensions:
        if dim.column == metric.time_dimension:
            return dim.name
    return ""


def model_card(
    graph: SemanticGraph,
    *,
    question: str = "",
    max_metrics: int = 60,
) -> str:
    """A text catalogue of the published model for the planning prompt."""
    by_key = {e.key: e for e in graph.entities}
    metrics = queryable_metrics(graph)

    total = len(metrics)
    trimmed = False
    if len(metrics) > max_metrics:
        wanted = _tokens(question)
        metrics = sorted(metrics, key=lambda m: _rank(m, wanted), reverse=True)[:max_metrics]
        trimmed = True

    lines: list[str] = []
    domain = graph.source_id
    lines.append(f"SOURCE: {domain}")
    lines.append("")
    lines.append("METRICS (use these exact names):")
    for metric in metrics:
        bits = [f'- "{metric.name}"']
        if metric.description:
            bits.append(f"— {metric.description.strip()}")
        line = " ".join(bits)
        when = _time_label(metric, by_key)
        if when:
            line += f" Measured by: {when}."
        if metric.kind.value == "derived":
            line += " [derived]"
        lines.append(line)
    if trimmed:
        # Say what was left out, and what to do about it — but only things the
        # model can actually do. The earlier wording invited it to "ask to list
        # more" when there is no tool to ask with, so the one honest move left
        # (clarify) went unmentioned.
        lines.append(
            f"(showing the {max_metrics} metrics most relevant to this question, "
            f"out of {total} published. If none of them is what the question "
            'means, answer with kind="clarify" and name what you were looking '
            "for — do not substitute a different metric.)"
        )

    # Which entities and dimensions are in play: the entities the shown metrics
    # measure, plus anything one join away (so the model knows what it can slice
    # by). Only visible dimensions — hidden ones aren't part of the model.
    owner_keys = {m.entity_key for m in metrics if m.entity_key}
    in_play = set(owner_keys)
    for rel in graph.relationships:
        if rel.from_entity_key in owner_keys:
            in_play.add(rel.to_entity_key)
        if rel.to_entity_key in owner_keys:
            in_play.add(rel.from_entity_key)

    dim_lines: list[str] = []
    for key in in_play:
        entity = by_key.get(key)
        if entity is None or entity.hidden:
            continue
        dims = [d.name for d in entity.dimensions if not d.hidden][:_MAX_DIMS_PER_ENTITY]
        if dims:
            dim_lines.append(f"- {entity.name}: {', '.join(dims)}")
    if dim_lines:
        lines.append("")
        lines.append(
            "DIMENSIONS (slice by these, grouped by entity). Several entities "
            'can share a name, so write one as "Entity.Name" whenever the '
            "question is not about the entity the metric measures:"
        )
        lines.extend(dim_lines)

    rels = [
        f"- {by_key[r.from_entity_key].name} → {by_key[r.to_entity_key].name}"
        for r in graph.relationships
        if r.from_entity_key in by_key and r.to_entity_key in by_key
    ]
    if rels:
        lines.append("")
        lines.append("RELATIONSHIPS:")
        lines.extend(rels)

    return "\n".join(lines)
