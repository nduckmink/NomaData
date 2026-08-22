"""What is actually stored in a dimension, remembered for a while.

The values of a column like ``Status`` change on the timescale of a schema
change, not a question. Asking the warehouse once and keeping the answer for an
hour costs one query per dimension per hour; asking every time would put a
round trip in front of every filter the agent writes.

Deliberately a dictionary and not Redis. A miss costs one fast query, so a
restart or a second worker losing its copy is invisible. When this becomes
several processes answering the same source and the extra queries start to
show, the same interface can be backed by the app database — the caller does
not change.
"""

from __future__ import annotations

import time
from typing import Any

#: How long a value list stays usable. Long enough that a conversation never
#: pays twice; short enough that a new status shows up the same working day.
TTL_SECONDS = 3600

#: A guard against remembering a column that turned out to have thousands of
#: values, and against a long-lived process growing without bound.
MAX_ENTRIES = 500


class ValueCache:
    def __init__(self, ttl_seconds: float = TTL_SECONDS, max_entries: int = MAX_ENTRIES) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._entries: dict[tuple[str, str], tuple[float, list[Any]]] = {}

    def get(self, source_id: str, member: str) -> list[Any] | None:
        """The remembered values, or ``None`` if they were never asked for or
        have gone stale. An empty list is a real answer — the column is empty —
        and is returned as one."""
        found = self._entries.get((source_id, member))
        if found is None:
            return None
        stored_at, values = found
        if time.monotonic() - stored_at > self._ttl:
            del self._entries[(source_id, member)]
            return None
        return values

    def put(self, source_id: str, member: str, values: list[Any]) -> None:
        if len(self._entries) >= self._max:
            # Drop the oldest rather than refuse to remember anything new.
            oldest = min(self._entries, key=lambda k: self._entries[k][0])
            del self._entries[oldest]
        self._entries[(source_id, member)] = (time.monotonic(), list(values))

    def clear(self) -> None:
        self._entries.clear()


#: One cache for the process. The agent is constructed per request, so it
#: cannot hold this itself.
VALUES = ValueCache()
