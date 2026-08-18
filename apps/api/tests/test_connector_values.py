"""Driver values → JSON-safe primitives.

The NUL cases are a real failure, not a hypothetical: one sampled column value
containing ``\\x00`` made PostgreSQL reject the whole semantic graph with
*"unsupported Unicode escape sequence"*, so an entire model build died on a
single byte.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from nomadata.connectors.values import strip_nul, to_jsonable
from nomadata.core.models import SemanticGraph
from nomadata.storage.semantic_repo import _jsonb


def test_strips_nul_from_text() -> None:
    assert to_jsonable("DA\x00THU") == "DATHU"


def test_strips_nul_from_decoded_bytes() -> None:
    assert to_jsonable(b"abc\x00") == "abc"


def test_strips_nul_from_stringified_objects() -> None:
    class Odd:
        def __str__(self) -> str:
            return "x\x00y"

    assert to_jsonable(Odd()) == "xy"


def test_leaves_ordinary_values_alone() -> None:
    assert to_jsonable(None) is None
    assert to_jsonable(7) == 7
    assert to_jsonable(True) is True
    assert to_jsonable("PAID") == "PAID"
    assert to_jsonable(Decimal("1.5")) == 1.5
    assert to_jsonable(datetime(2026, 8, 17, 10, 30)) == "2026-08-17T10:30:00"
    assert to_jsonable(date(2026, 8, 17)) == "2026-08-17"


def test_strip_nul_returns_the_same_string_when_clean() -> None:
    text = "nothing to do"
    assert strip_nul(text) is text


def test_graph_serialization_never_emits_a_nul_escape() -> None:
    """Second line of defence: a graph can also arrive from the client or from a
    model response, and one stray byte must not fail the write."""
    graph = SemanticGraph.model_validate(
        {
            "source_id": "scp",
            "entities": [
                {
                    "key": "public.t",
                    "name": "T\x00",
                    "table": "t",
                    "dimensions": [
                        {
                            "name": "Status",
                            "column": "status",
                            "sample_values": ["A\x00B"],
                        }
                    ],
                }
            ],
        }
    )

    payload = _jsonb(graph)

    assert "\\u0000" not in payload
    assert "A" in payload and "B" in payload
