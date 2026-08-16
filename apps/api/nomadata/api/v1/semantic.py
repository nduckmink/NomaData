"""Semantic model endpoints — draft, versions, publish.

The AI (M2.2) proposes a draft; a human reviews and publishes here. The model is
persisted in the app database, versioned.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from nomadata.core.errors import (
    DataSourceNotFoundError,
    SemanticModelNotConfiguredError,
)
from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    EnrichmentHints,
    PublishResult,
    SemanticGraph,
    SemanticModelVersion,
)
from nomadata.core.registry import get_registry
from nomadata.semantic.suggester import SemanticSuggester

router = APIRouter(prefix="/datasources/{name}/semantic", tags=["semantic"])


def _service() -> SemanticModel:
    try:
        return get_registry().get_semantic_model()
    except SemanticModelNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _source(name: str) -> DataSource:
    try:
        return get_registry().get_data_source(name)
    except DataSourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Data source not found: {name!r}") from None


def _ai_provider() -> AIProvider | None:
    """The active AI provider, or None if AI is unconfigured (heuristic only)."""
    return get_registry().active_provider()


@router.get("", response_model=SemanticGraph | None)
async def get_semantic(name: str) -> SemanticGraph | None:
    """The latest draft for a source, or ``null`` if none exists yet.

    "No model yet" is an ordinary empty state, so it returns 200 with a null
    body — not 404. A 404 here means the data *source* itself is unknown (a real
    error), which is kept distinct so it can't be swallowed as "just empty"."""
    service = _service()
    _source(name)  # 404 only when the data source truly doesn't exist
    return await service.get_draft(name)


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


@router.delete("", status_code=204)
async def delete_semantic(name: str) -> Response:
    """Delete a source's semantic model (all versions)."""
    await _service().delete(name)
    return Response(status_code=204)


@router.post("/enrich", response_model=EnrichmentHints)
async def enrich_semantic(name: str, graph: SemanticGraph) -> EnrichmentHints:
    """Return compact AI business text (name + description/definition) for the
    entities/metrics in the posted graph, matched back by table/formula. The
    caller merges it (fills blanks / replaces defaults). Send a manageable slice
    — the caller batches. Requires a configured AI provider."""
    provider = _ai_provider()
    if provider is None:
        raise HTTPException(
            status_code=409,
            detail="AI provider not configured — set one in Settings.",
        )
    suggester = SemanticSuggester(provider)
    return await suggester.enrich_hints(graph)


@router.post("/suggest", response_model=SemanticGraph)
async def suggest_semantic(name: str, use_ai: bool = True) -> SemanticGraph:
    """Introspect the source schema and propose a draft semantic model.

    Uses AI enrichment when a provider is configured (``use_ai=true``, default),
    otherwise the heuristic baseline. The result is a *suggestion* — it is not
    saved; a human reviews it (PUT) and publishes it separately."""
    catalog = await _source(name).inspect_schema()
    suggester = SemanticSuggester(_ai_provider())
    graph = await suggester.suggest(catalog, use_ai=use_ai)
    return graph.model_copy(update={"source_id": name})
