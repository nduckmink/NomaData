"""Cross-source semantic overview — one row per data source.

Powers the global `/semantic` page: every data source with its model status
(none / draft / published), version, provenance and shape. Sources without a
model are included so the UI can offer to generate one. This is an *index* of
per-source models, not a merged cross-database model (that would need the
multi-source data model in a later phase).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from nomadata.core.errors import SemanticModelNotConfiguredError
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import SemanticGraph, SemanticModelSummary
from nomadata.core.registry import get_registry
from nomadata.semantic.service import SemanticModelNotFoundError
from nomadata.semantic.validator import validate_graph

router = APIRouter(prefix="/semantic", tags=["semantic"])


def _service() -> SemanticModel:
    try:
        return get_registry().get_semantic_model()
    except SemanticModelNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


async def _published_or_none(service: SemanticModel, name: str) -> SemanticGraph | None:
    try:
        return await service.load(name)
    except SemanticModelNotFoundError:
        return None


@router.get("", response_model=list[SemanticModelSummary])
async def semantic_overview(request: Request) -> list[SemanticModelSummary]:
    service = _service()
    manager = getattr(request.app.state, "datasource_manager", None)
    summaries: list[SemanticModelSummary] = []

    for name in get_registry().data_source_names():
        kind = None
        if manager is not None:
            info = await manager.get_info(name)
            kind = info.kind if info else None

        latest = await service.get_draft(name)  # newest version, any status
        if latest is None:
            summaries.append(SemanticModelSummary(source_id=name, kind=kind))
            continue

        published = await _published_or_none(service, name)
        # Structural health only — no catalog, so no database hit. This is the
        # same check the Publish button runs; here it is a cheap preview of
        # whether the model would pass. "Still matches the live schema" is a
        # separate, costlier question (it needs introspection).
        report = validate_graph(latest)
        # A draft ahead of what is live: the newest version is a draft, and
        # either nothing is published or the published version is older.
        unpublished = not latest.published and (
            published is None or latest.version > published.version
        )
        summaries.append(
            SemanticModelSummary(
                source_id=name,
                kind=kind,
                has_model=True,
                status="published" if latest.published else "draft",
                latest_version=latest.version,
                published_version=published.version if published else None,
                provenance=latest.provenance,
                entity_count=len(latest.entities),
                metric_count=len(latest.metrics),
                relationship_count=len(latest.relationships),
                error_count=len(report.errors),
                warning_count=len(report.warnings),
                has_unpublished_changes=unpublished,
            )
        )

    return summaries
