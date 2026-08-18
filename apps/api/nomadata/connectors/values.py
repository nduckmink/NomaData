"""Coercing driver-native values into JSON-safe primitives.

Shared by every connector: what comes back from a driver (Decimal, datetime,
bytes, driver-specific objects) has to survive being stored as JSON and sent
over HTTP.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any

_NUL = "\x00"


def strip_nul(text: str) -> str:
    """Remove NUL characters.

    PostgreSQL rejects ``\\u0000`` in ``text`` and ``jsonb`` outright. A single
    NUL byte in one sampled column value therefore used to kill a whole model
    build with *"unsupported Unicode escape sequence"* — the profiled value went
    into the semantic graph, and the graph could no longer be saved. Dropping it
    at the point the value leaves the driver keeps that impossible.
    """
    return text.replace(_NUL, "") if _NUL in text else text


def to_jsonable(value: Any) -> Any:
    """Coerce a driver-native value into a JSON-serializable primitive."""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return strip_nul(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return strip_nul(value.decode("utf-8", "replace"))
    return strip_nul(str(value))
