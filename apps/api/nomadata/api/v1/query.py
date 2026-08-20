"""Analytical query endpoint — run an AnalyticalQuery through the query engine.

The client (later, the agent) sends an ``AnalyticalQuery`` naming metrics and
dimensions in **business language** — the same names a person reviewed and
published. The engine translates them and Cube executes. Never SQL over the
wire, and never the engine's own identifiers either.

The path's data source decides which published model those names are read
against: the same word can mean different things in two databases, so answering
from the wrong one would be a silently wrong number.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from nomadata.agent.resolver import QueryValidationError
from nomadata.core.errors import (
    QueryEngineNotConfiguredError,
    SemanticModelNotConfiguredError,
)
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    AnalyticalQuery,
    BusinessContext,
    QueryResult,
    SemanticGraph,
)
from nomadata.core.registry import get_registry
from nomadata.query.cube import QueryEngineError
from nomadata.semantic.service import SemanticModelNotFoundError

router = APIRouter(prefix="/datasources/{name}/query", tags=["query"])


def _engine() -> QueryEngine:
    try:
        return get_registry().get_query_engine()
    except QueryEngineNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _semantic() -> SemanticModel:
    try:
        return get_registry().get_semantic_model()
    except SemanticModelNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


async def _published(name: str) -> SemanticGraph:
    """The live model for a source. A draft is deliberately not accepted: the
    query layer answers from what was published, the same rule the Cube files
    follow."""
    try:
        return await _semantic().load(name)
    except SemanticModelNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"No published semantic model for {name!r} — publish one first.",
        ) from None


async def _with_timezone(request: Request, name: str, query: AnalyticalQuery) -> AnalyticalQuery:
    """Stamp the source's timezone onto the query's time window.

    A relative period ("this month") is meaningless without one, and Cube
    defaults to UTC: in UTC+7 that puts the first seven hours of every day in
    the day before. The caller may state a zone explicitly; otherwise the one
    configured for this data source applies.
    """
    if query.time is None or query.time.timezone:
        return query
    repo = getattr(request.app.state, "semantic_contexts", None)
    if repo is None:
        return query
    context: BusinessContext | None = await repo.get(name)
    if context is None:
        return query
    return query.model_copy(
        update={"time": query.time.model_copy(update={"timezone": context.timezone})}
    )


@router.post("", response_model=QueryResult)
async def run_query(request: Request, name: str, query: AnalyticalQuery) -> QueryResult:
    graph = await _published(name)
    query = await _with_timezone(request, name, query)
    try:
        return await _engine().run(query, graph)
    except QueryValidationError as exc:
        # A name that isn't in the model: the caller can fix this, and the
        # message already carries the nearest real name.
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except QueryEngineError as exc:
        # A query the engine could not run — still the caller's, not a crash.
        raise HTTPException(status_code=400, detail=str(exc)) from None
