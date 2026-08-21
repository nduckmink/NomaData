"""Read back the question threads for a source.

Asking is served by ``/ask``; this is what makes the asking reviewable — the
list of threads, and one thread in full with the query and the model version
behind every number in it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from nomadata.core.models import Conversation

router = APIRouter(prefix="/datasources/{name}/conversations", tags=["ask"])


def _repo(request: Request) -> Any:
    repo = getattr(request.app.state, "conversations", None)
    if repo is None:
        raise HTTPException(
            status_code=503,
            detail="Conversations need the app database, which is not connected.",
        )
    return repo


@router.get("", response_model=list[Conversation])
async def list_conversations(request: Request, name: str, limit: int = 50) -> list[Conversation]:
    """Threads on this source, most recently used first, without their turns."""
    return await _repo(request).list(name, limit=max(1, min(limit, 200)))


@router.get("/{conversation_id}", response_model=Conversation)
async def get_conversation(request: Request, name: str, conversation_id: str) -> Conversation:
    conversation = await _repo(request).get(conversation_id)
    # Checking the source as well as the id: a thread belongs to the model it
    # was asked against, and its metric names mean nothing under another.
    if conversation is None or conversation.source_id != name:
        raise HTTPException(status_code=404, detail=f"No conversation {conversation_id!r}.")
    return conversation


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(request: Request, name: str, conversation_id: str) -> None:
    repo = _repo(request)
    conversation = await repo.get(conversation_id)
    if conversation is None or conversation.source_id != name:
        raise HTTPException(status_code=404, detail=f"No conversation {conversation_id!r}.")
    await repo.delete(conversation_id)
