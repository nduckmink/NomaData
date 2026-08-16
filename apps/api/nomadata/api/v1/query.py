"""Analytical query endpoint — run an AnalyticalQuery through the query engine.

The client (later, the agent) sends an ``AnalyticalQuery`` (measures / dimensions
/ filters / time) referencing the published semantic model; the engine (Cube)
executes it and returns rows. Never SQL over the wire.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from nomadata.core.errors import QueryEngineNotConfiguredError
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.models import AnalyticalQuery, QueryResult
from nomadata.core.registry import get_registry
from nomadata.query.cube import QueryEngineError

router = APIRouter(prefix="/datasources/{name}/query", tags=["query"])


def _engine() -> QueryEngine:
    try:
        return get_registry().get_query_engine()
    except QueryEngineNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.post("", response_model=QueryResult)
async def run_query(name: str, query: AnalyticalQuery) -> QueryResult:
    try:
        return await _engine().run(query)
    except QueryEngineError as exc:
        # A bad query / unknown member is the caller's fault, not a server crash.
        raise HTTPException(status_code=400, detail=str(exc)) from None
