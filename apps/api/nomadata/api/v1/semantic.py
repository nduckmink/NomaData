"""Semantic model endpoints — draft, versions, publish.

The AI (M2.2) proposes a draft; a human reviews and publishes here. The model is
persisted in the app database, versioned.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from nomadata.core.errors import SemanticModelNotConfiguredError
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    PublishResult,
    SemanticGraph,
    SemanticModelVersion,
)
from nomadata.core.registry import get_registry

router = APIRouter(prefix="/datasources/{name}/semantic", tags=["semantic"])


def _service() -> SemanticModel:
    try:
        return get_registry().get_semantic_model()
    except SemanticModelNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.get("", response_model=SemanticGraph)
async def get_semantic(name: str) -> SemanticGraph:
    graph = await _service().get_draft(name)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"No semantic model for {name!r}")
    return graph


@router.put("", response_model=SemanticGraph)
async def put_semantic(name: str, graph: SemanticGraph) -> SemanticGraph:
    # Pin the source_id to the path so body and route can't disagree.
    return await _service().save_draft(graph.model_copy(update={"source_id": name}))


@router.post("/publish", response_model=PublishResult)
async def publish_semantic(name: str, graph: SemanticGraph) -> PublishResult:
    return await _service().publish(graph.model_copy(update={"source_id": name}))


@router.get("/versions", response_model=list[SemanticModelVersion])
async def list_versions(name: str) -> list[SemanticModelVersion]:
    return await _service().list_versions(name)
