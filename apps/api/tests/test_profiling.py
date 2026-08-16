"""Categorical-column detection heuristic."""

from __future__ import annotations

from nomadata.connectors.profiling import detect_categorical


def test_low_cardinality_is_categorical() -> None:
    # a status column: 3 values across many rows
    assert detect_categorical(3, 10_000) is True
    assert detect_categorical(40, 200) is True  # ≤50 distinct, ratio 0.2


def test_key_and_high_cardinality_are_not_categorical() -> None:
    assert detect_categorical(100, 100) is False  # unique key (ratio 1.0)
    assert detect_categorical(500, 10_000) is False  # too many distinct (>50)


def test_missing_info_is_none() -> None:
    assert detect_categorical(None, 100) is None
    assert detect_categorical(5, 0) is None  # empty table
    assert detect_categorical(0, 100) is None
