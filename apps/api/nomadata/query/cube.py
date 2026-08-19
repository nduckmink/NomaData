"""Cube QueryEngine adapter — runs an AnalyticalQuery through Cube's REST API.

The app produces an ``AnalyticalQuery`` (measures / dimensions / filters / time),
never SQL. This adapter translates it into a Cube load query, signs the API JWT,
posts it to Cube, and returns a ``QueryResult``. Cube-specific concepts stay
here. Single-source MVP.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import jwt

from nomadata.core.errors import NomaDataError
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.models import (
    AnalyticalQuery,
    ExecutionPlan,
    Filter,
    QueryResult,
    ResultColumn,
)
from nomadata.logging import get_logger

log = get_logger()

# NomaData filter operator -> Cube filter operator.
#
# Every member of ``FILTER_OPERATORS`` must appear here. A missing entry used to
# fall back to "equals", which turns `not_in` into its own opposite and answers
# with a number that looks entirely normal — the failure mode this codebase
# treats as worse than a crash. A test walks the whole set so adding an operator
# without teaching this adapter fails loudly.
#
# Cube's `equals` takes a list of values, so it is also the correct translation
# of `in` (and `notEquals` of `not_in`).
_FILTER_OP: dict[str, str] = {
    "eq": "equals",
    "neq": "notEquals",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    "in": "equals",
    "not_in": "notEquals",
    "contains": "contains",
    "set": "set",
    "not_set": "notSet",
}

#: Operators that assert on presence, not on a value — Cube rejects a `values`
#: key for these.
_VALUELESS_CUBE_OPS = frozenset({"set", "notSet"})

#: Rows a single query may return. Without a ceiling, "list every contract"
#: pulls Cube's default 10,000 rows through the API and, in an agent loop, into
#: the model's context on the next turn.
MAX_ROWS = 1000

#: Rows requested when the caller names no limit.
DEFAULT_ROWS = 200


class QueryEngineError(NomaDataError):
    """Cube rejected the query or could not be reached."""


def _filter(f: Filter) -> dict[str, Any]:
    operator = _FILTER_OP.get(f.operator)
    if operator is None:
        # Never guess: a wrong operator produces a plausible, wrong number.
        raise QueryEngineError(
            f"Filter operator {f.operator!r} cannot be run by the query engine."
        )
    if operator in _VALUELESS_CUBE_OPS:
        return {"member": f.field, "operator": operator}
    values = f.value if isinstance(f.value, list) else [f.value]
    return {
        "member": f.field,
        "operator": operator,
        "values": [str(v) for v in values],
    }


def row_limit(query: AnalyticalQuery) -> int:
    """The row ceiling actually applied — the caller's limit, capped."""
    requested = query.limit if query.limit and query.limit > 0 else DEFAULT_ROWS
    return min(requested, MAX_ROWS)


def build_cube_query(query: AnalyticalQuery) -> dict[str, Any]:
    """Translate an AnalyticalQuery into a Cube load query."""
    cube: dict[str, Any] = {}
    if query.measures:
        cube["measures"] = list(query.measures)
    if query.dimensions:
        cube["dimensions"] = list(query.dimensions)
    if query.filters:
        cube["filters"] = [_filter(f) for f in query.filters]
    if query.time is not None:
        td: dict[str, Any] = {"dimension": query.time.dimension}
        if query.time.grain is not None:
            td["granularity"] = str(query.time.grain)
        if query.time.range:
            td["dateRange"] = query.time.range.replace("_", " ")
        cube["timeDimensions"] = [td]
    # Always send a limit: an absent one means Cube's own default, which is
    # far larger than anything a caller here is prepared to handle.
    cube["limit"] = row_limit(query)
    if query.order_by:
        cube["order"] = [
            [o.lstrip("-"), "desc" if o.startswith("-") else "asc"] for o in query.order_by
        ]
    return cube


class CubeQueryEngine(QueryEngine):
    def __init__(self, base_url: str, api_secret: str, timeout: float = 60.0) -> None:
        self._url = base_url.rstrip("/")
        self._secret = api_secret
        self._timeout = timeout

    async def plan(self, query: AnalyticalQuery) -> ExecutionPlan:
        return ExecutionPlan(source_id="cube", representation=build_cube_query(query))

    async def run(self, query: AnalyticalQuery) -> QueryResult:
        plan = await self.plan(query)
        token = jwt.encode({}, self._secret, algorithm="HS256")
        headers = {"Authorization": token}
        body = {"query": plan.representation}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                data = await self._load(client, headers, body)
        except httpx.HTTPError as exc:
            raise QueryEngineError(f"Cube request failed: {exc}") from exc

        rows: list[dict[str, Any]] = data.get("data", [])
        columns = self._columns(rows, data)
        # Hitting the ceiling means there was probably more; say so rather than
        # letting a partial answer read as a complete one.
        limit = row_limit(query)
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=len(rows) >= limit,
        )

    async def _load(
        self, client: httpx.AsyncClient, headers: dict[str, str], body: dict[str, Any]
    ) -> dict[str, Any]:
        # Cube answers a not-yet-ready query with {"error": "Continue wait"} and
        # HTTP 200 — retry a few times before giving up.
        for _ in range(10):
            resp = await client.post(f"{self._url}/cubejs-api/v1/load", json=body, headers=headers)
            payload: dict[str, Any] = resp.json() if resp.content else {}
            if resp.status_code == 200 and payload.get("error") != "Continue wait":
                if "error" in payload:
                    raise QueryEngineError(str(payload["error"]))
                return payload
            if resp.status_code >= 400:
                raise QueryEngineError(
                    f"Cube returned {resp.status_code}: {payload.get('error', resp.text[:200])}"
                )
            await asyncio.sleep(1)
        raise QueryEngineError("Cube query did not complete in time.")

    @staticmethod
    def _columns(rows: list[dict[str, Any]], data: dict[str, Any]) -> list[ResultColumn]:
        annotation = data.get("annotation", {})
        members = {**annotation.get("measures", {}), **annotation.get("dimensions", {})}
        if members:
            return [
                ResultColumn(name=key, data_type=str(meta.get("type", "")))
                for key, meta in members.items()
            ]
        keys = list(rows[0].keys()) if rows else []
        return [ResultColumn(name=k, data_type="") for k in keys]
