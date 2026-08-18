"""Find joins the database never declared.

Foreign keys are the reliable source of relationships, and where they exist the
build already uses them. But they are frequently absent: the SQL Server source
here has 196 tables and 12 foreign keys, which leaves almost every question that
crosses two tables unanswerable.

The convention that survives in such schemas is naming — ``enterprise_id``
points at ``enterprises.id``. This module reads that convention. It is
deliberately rule-based rather than a model call: the answer is checkable, it
costs nothing, and a wrong guess here would quietly join unrelated rows, so
"plausible" is not good enough. Anything it finds is a *proposal* the user
accepts, exactly like an AI suggestion.
"""

from __future__ import annotations

import re

from nomadata.core.models import Entity, Relationship, SemanticGraph

#: `enterprise_id`, `employeeId`, `ma_hoc_sinh_id` → the part before the suffix.
_ID_SUFFIX = re.compile(r"^(?P<stem>.+?)[_]?(?:id|Id|ID)$")

#: Plural forms a table name is likely to take for a singular column stem.
_PLURAL_SUFFIXES = ("s", "es", "ies")


def _stem(column: str) -> str | None:
    match = _ID_SUFFIX.match(column)
    if not match:
        return None
    stem = match.group("stem").strip("_").lower()
    return stem or None


def _table_aliases(table: str) -> set[str]:
    """The names a column stem might use for this table.

    ``enterprises`` is referenced as ``enterprise_id``; ``scp_hoc_phi`` as
    ``hoc_phi_id``. Both the full name and the de-prefixed, de-pluralised forms
    are accepted.
    """
    lowered = table.lower()
    forms = {lowered}
    # Drop a leading module prefix: `scp_hoc_phi` is referred to as `hoc_phi`.
    if "_" in lowered:
        forms.add(lowered.split("_", 1)[1])
    for form in set(forms):
        if form.endswith("ies"):
            forms.add(form[:-3] + "y")
        for suffix in _PLURAL_SUFFIXES:
            if form.endswith(suffix) and len(form) > len(suffix) + 1:
                forms.add(form[: -len(suffix)])
    return forms


def suggest_relationships(graph: SemanticGraph) -> list[Relationship]:
    """Joins implied by column names that the model does not already have.

    Only columns that look like a key (``*_id``) are considered, and only when
    exactly one entity matches the name — an ambiguous stem is left alone rather
    than guessed.
    """
    entities = [e for e in graph.entities if not e.hidden]
    by_alias: dict[str, list[Entity]] = {}
    for entity in entities:
        for alias in _table_aliases(entity.table):
            by_alias.setdefault(alias, []).append(entity)

    existing = {
        (r.from_entity_key, r.from_column, r.to_entity_key) for r in graph.relationships
    }
    found: list[Relationship] = []
    seen: set[tuple[str, str, str]] = set()

    for entity in entities:
        for dimension in entity.dimensions:
            stem = _stem(dimension.column)
            if not stem:
                continue
            candidates = by_alias.get(stem, [])
            # Two tables answering to the same stem means we cannot tell which
            # was meant; a wrong join is worse than a missing one.
            if len(candidates) != 1:
                continue
            target = candidates[0]
            if target.key == entity.key:
                continue
            signature = (entity.key, dimension.column, target.key)
            if signature in existing or signature in seen:
                continue
            seen.add(signature)
            found.append(
                Relationship(
                    from_entity_key=entity.key,
                    to_entity_key=target.key,
                    from_column=dimension.column,
                    to_column=target.primary_key,
                    kind="many_to_one",
                )
            )
    return found
