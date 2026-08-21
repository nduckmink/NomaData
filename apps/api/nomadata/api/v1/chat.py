"""Chat: a question in natural language against a source's published model.

The synchronous first cut of the conversational engine: question in, one
``AgentTurn`` out (an answer, a clarification, a refusal, or a clean error).
Conversation history and persistence come later; this proves the loop.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from nomadata.agent.history import MAX_HISTORY_TURNS
from nomadata.agent.resolver import QueryValidationError
from nomadata.agent.runtime import AgentRuntime, StepSink
from nomadata.core.errors import (
    QueryEngineNotConfiguredError,
    SemanticModelNotConfiguredError,
)
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    AgentStep,
    AgentTurn,
    BusinessContext,
    ChatRequest,
    ConversationTurn,
    SemanticGraph,
)
from nomadata.core.registry import get_registry
from nomadata.query.cube import QueryEngineError
from nomadata.semantic.service import SemanticModelNotFoundError

router = APIRouter(prefix="/datasources/{name}/chat", tags=["chat"])


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


def _conversations(request: Request) -> Any:
    """The conversation store, or ``None`` when the app DB is unavailable.

    Asking is more important than remembering: without the store the agent still
    answers, it just cannot follow up. Failing the question instead would take
    away the working half along with the broken one.
    """
    return getattr(request.app.state, "conversations", None)


async def _thread(request: Request, name: str, body: ChatRequest) -> tuple[Any, str]:
    """The conversation this question belongs to, started if it is the first."""
    repo = _conversations(request)
    if repo is None:
        return None, ""
    wanted = (body.conversation_id or "").strip()
    if wanted:
        # Only on the source it was started against: history from another model
        # describes metrics whose names mean something else here.
        if await repo.exists(wanted, name):
            return repo, wanted
        raise HTTPException(
            status_code=404,
            detail=f"No conversation {wanted!r} on {name!r}.",
        )
    return repo, await repo.start(name, body.question.strip())


async def _history(repo: Any, conversation_id: str) -> list[ConversationTurn]:
    if repo is None or not conversation_id:
        return []
    return await repo.recent_turns(conversation_id, limit=MAX_HISTORY_TURNS)


async def _run(
    request: Request,
    name: str,
    body: ChatRequest,
    on_step: StepSink | None = None,
) -> AgentTurn:
    """One question, answered and stored. Shared by the plain and streaming routes."""
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
    repo, conversation_id = await _thread(request, name, body)

    try:
        turn = await runtime.answer(
            question,
            graph,
            context=context,
            history=await _history(repo, conversation_id),
            on_step=on_step,
        )
    except QueryValidationError as exc:
        # The model named something the published model does not have, past the
        # repair turns. That is a modelling gap, not a provider outage — calling
        # it "the AI provider failed" sent everyone looking in the wrong place.
        turn = AgentTurn(kind="error", question=question, reason=str(exc))
    except QueryEngineError as exc:
        turn = AgentTurn(kind="error", question=question, reason=str(exc))

    turn.conversation_id = conversation_id
    if repo is not None and conversation_id:
        # Including the turns that failed. A question the agent could not answer
        # is the most useful row in this table — it is the list of what the
        # model is missing, and nothing else records it.
        turn.ordinal = await repo.append(conversation_id, turn)
    return turn


@router.post("", response_model=AgentTurn)
async def chat(request: Request, name: str, body: ChatRequest) -> AgentTurn:
    try:
        return await _run(request, name, body)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - the LLM/provider round trip failed
        raise HTTPException(status_code=502, detail=f"The AI provider failed: {exc}") from exc


@router.post("/stream")
async def chat_stream(request: Request, name: str, body: ChatRequest) -> StreamingResponse:
    """The same turn, but the work is visible while it happens.

    What streams is the agent's progress, not the answer being written: the
    number and the "read from" line are computed once the query has run, so
    there is no text being composed to send a word at a time. Ten seconds of
    silence is the thing worth fixing, and this fixes that.

    Steps go out as they occur; the finished turn is the last event. A failure
    is an event too — an SSE response has already sent its 200, so an error
    raised halfway would otherwise reach the browser as a truncated stream.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def emit(step: AgentStep) -> None:
        await queue.put(_event("step", step.model_dump_json()))

    async def work() -> None:
        try:
            turn = await _run(request, name, body, on_step=emit)
            await queue.put(_event("turn", turn.model_dump_json()))
        except HTTPException as exc:
            await queue.put(_event("error", json.dumps({"detail": exc.detail})))
        except Exception as exc:  # noqa: BLE001 - report it, don't cut the stream
            await queue.put(
                _event("error", json.dumps({"detail": f"The AI provider failed: {exc}"}))
            )
        finally:
            await queue.put(None)

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(work())
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    return
                yield chunk
        finally:
            # The reader went away — stop paying for a turn nobody will read.
            if not task.done():
                task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _event(name: str, data: str) -> str:
    return f"event: {name}\ndata: {data}\n\n"
