"""Ask a question in natural language against a source's published model.

The synchronous first cut of the conversational engine: question in, one
``AgentTurn`` out (an answer, a clarification, a refusal, or a clean error).
Conversation history and persistence come later; this proves the loop.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from nomadata.agent.resolver import queryable_metrics
from nomadata.agent.runtime import AgentRuntime
from nomadata.core.errors import (
    QueryEngineNotConfiguredError,
    SemanticModelNotConfiguredError,
)
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import AgentTurn, AskRequest, BusinessContext, SemanticGraph
from nomadata.core.registry import get_registry
from nomadata.semantic.service import SemanticModelNotFoundError

router = APIRouter(prefix="/datasources/{name}/ask", tags=["ask"])


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
    try:
        return await _semantic().load(name)
    except SemanticModelNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"No published semantic model for {name!r} — publish one first.",
        ) from None


async def _context(request: Request, name: str) -> BusinessContext | None:
    repo = getattr(request.app.state, "semantic_contexts", None)
    if repo is None:
        return None
    return await repo.get(name)


def _examples(graph: SemanticGraph) -> list[str]:
    """A few real questions to try, drawn from the published model — ground
    truth, so the UI never suggests a metric the model can't answer."""
    metrics = queryable_metrics(graph)
    if not metrics:
        return []
    by_key = {e.key: e for e in graph.entities}
    out: list[str] = [m.name for m in metrics[:3]]
    first = metrics[0]
    entity = by_key.get(first.entity_key or "")
    if entity is not None:
        dim = next(
            (d for d in entity.dimensions if not d.hidden and d.column != first.column),
            None,
        )
        if dim is not None:
            out.append(f"{first.name} by {dim.name}")
    return list(dict.fromkeys(out))[:5]


@router.get("/examples")
async def examples(name: str) -> list[str]:
    """Suggested questions for a source (empty list if it has no published model)."""
    try:
        return _examples(await _published(name))
    except HTTPException:
        return []


@router.post("", response_model=AgentTurn)
async def ask(request: Request, name: str, body: AskRequest) -> AgentTurn:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question.")

    graph = await _published(name)
    provider = get_registry().active_provider()
    if provider is None:
        raise HTTPException(
            status_code=409,
            detail="No AI provider is configured. Add one in Settings to ask "
            "questions in natural language.",
        )

    runtime = AgentRuntime(provider, _engine())
    context = await _context(request, name)
    try:
        return await runtime.answer(question, graph, context=context)
    except Exception as exc:  # noqa: BLE001 - the LLM/provider round trip failed
        raise HTTPException(status_code=502, detail=f"The AI provider failed: {exc}") from exc
