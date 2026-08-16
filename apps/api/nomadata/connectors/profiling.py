"""Shared column-profiling helpers used by every connector."""

from __future__ import annotations

# A column is a likely category (status, type, region...) when it has few
# distinct values both in absolute terms and relative to the row count. A
# high-cardinality or unique column (a key) is not categorical.
CATEGORICAL_MAX_DISTINCT = 50
CATEGORICAL_MAX_RATIO = 0.5


def detect_categorical(distinct_count: int | None, total: int) -> bool | None:
    """Whether a column looks categorical. None when there isn't enough info
    (unknown distinct count, or an empty table)."""
    if distinct_count is None or total <= 0 or distinct_count <= 0:
        return None
    return (
        distinct_count <= CATEGORICAL_MAX_DISTINCT
        and distinct_count / total <= CATEGORICAL_MAX_RATIO
    )
