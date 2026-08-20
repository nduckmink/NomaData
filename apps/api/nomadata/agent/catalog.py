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

from nomadata.agent.resolver import queryable_metrics
from nomadata.core.models import Entity, MetricDefinition, SemanticGraph

_WORD = re.compile(r"[A-Za-zÀ-ỹ0-9_]+")
_MAX_DIMS_PER_ENTITY = 24


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text or "") if len(t) > 1}


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

    trimmed = False
    if len(metrics) > max_metrics:
        wanted = _tokens(question)
        metrics = sorted(
            metrics,
            key=lambda m: len(wanted & _tokens(f"{m.name} {m.description or ''}")),
            reverse=True,
        )[:max_metrics]
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
        lines.append(
            f"(only the {max_metrics} metrics most relevant to the question are "
            "shown; ask to list more if what you need is missing)"
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
        lines.append("DIMENSIONS (slice by these, grouped by entity):")
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
