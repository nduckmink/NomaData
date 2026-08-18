"""Semantic model endpoints — build, review, validate, publish.

The AI proposes a draft; a human reviews and publishes here. The model is
persisted in the app database and versioned, and a publish is gated on the model
actually being executable — a graph Cube cannot run must not be able to claim it
is live.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from nomadata.config import get_settings
from nomadata.core.errors import (
    DataSourceNotFoundError,
    SemanticModelNotConfiguredError,
)
from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    BusinessContext,
    DatabaseCatalog,
    EntityDraftRequest,
    EntityDraftResponse,
    GenerationJob,
    MetricDefinition,
    MetricDraftRequest,
    MetricDraftResponse,
    MetricPreview,
    MetricSuggestRequest,
    MetricSuggestResponse,
    PublishResult,
    Relationship,
    SemanticGraph,
    SemanticModelVersion,
    ValidationReport,
)
from nomadata.core.registry import get_registry
from nomadata.logging import get_logger
from nomadata.query.cube_schema import CubeCompileError, remove_cube_model, write_cube_model
from nomadata.semantic.drafter import EntityDrafter, MetricDrafter, MetricSuggester
from nomadata.semantic.jobs import SemanticJobRunner
from nomadata.semantic.preview import preview_metric
from nomadata.semantic.relationships import suggest_relationships
from nomadata.semantic.validator import validate_graph
from nomadata.storage.semantic_repo import SemanticConflictError

log = get_logger()

router = APIRouter(prefix="/datasources/{name}/semantic", tags=["semantic"])


def _jobs(request: Request) -> SemanticJobRunner:
    runner = getattr(request.app.state, "semantic_jobs", None)
    if runner is None:
        raise HTTPException(
            status_code=503,
            detail="Semantic jobs unavailable — app database not connected.",
        )
    return runner


def _contexts(request: Request) -> object:
    repo = getattr(request.app.state, "semantic_contexts", None)
    if repo is None:
        raise HTTPException(
            status_code=503,
            detail="Business context unavailable — app database not connected.",
        )
    return repo


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


def _require_ai() -> AIProvider:
    provider = _ai_provider()
    if provider is None:
        raise HTTPException(
            status_code=409, detail="AI provider not configured — set one in Settings."
        )
    return provider


async def _catalog(name: str) -> DatabaseCatalog | None:
    """The live schema, when it can be reached. Validation is sharper with it
    and still useful without it, so an unreachable database is not fatal here."""
    try:
        return await _source(name).inspect_schema()
    except Exception as exc:  # noqa: BLE001 - validation degrades, it doesn't fail
        log.warning("semantic.catalog.unavailable", source=name, error=str(exc))
        return None


async def _context_for(request: Request, name: str) -> BusinessContext | None:
    """The business context, when the app DB is up. A prompt without it still
    works — it just has less to go on — so this never raises."""
    repo = getattr(request.app.state, "semantic_contexts", None)
    if repo is None:
        return None
    context: BusinessContext | None = await repo.get(name)
    return context


async def _load_draft(name: str) -> SemanticGraph:
    graph = await _service().get_draft(name)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"No semantic model for {name!r}.")
    return graph


# ----------------------------------------------------------------------
# Draft lifecycle
# ----------------------------------------------------------------------


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
async def put_semantic(
    name: str, graph: SemanticGraph, expected_revision: int | None = None
) -> SemanticGraph:
    # Pin the source_id to the path so body and route can't disagree.
    pinned = graph.model_copy(update={"source_id": name})
    try:
        return await _service().save_draft(pinned, expected_revision=expected_revision)
    except SemanticConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.delete("", status_code=204)
async def delete_semantic(name: str) -> Response:
    """Delete a source's semantic model (all versions) and its Cube file."""
    await _service().delete(name)
    try:
        remove_cube_model(name, get_settings().cube_model_dir)
    except OSError as exc:
        log.warning("semantic.cube.remove_failed", source=name, error=str(exc))
    return Response(status_code=204)


@router.get("/versions", response_model=list[SemanticModelVersion])
async def list_versions(name: str) -> list[SemanticModelVersion]:
    return await _service().list_versions(name)


# ----------------------------------------------------------------------
# Validation & publish
# ----------------------------------------------------------------------


@router.post("/validate", response_model=ValidationReport)
async def validate_semantic(name: str, graph: SemanticGraph | None = None) -> ValidationReport:
    """Check a graph (the posted one, or the saved draft) for anything that would
    break at query time. Errors block a publish; warnings are advisory."""
    target = graph or await _load_draft(name)
    return validate_graph(target.model_copy(update={"source_id": name}), await _catalog(name))


@router.post("/publish", response_model=PublishResult)
async def publish_semantic(name: str, graph: SemanticGraph) -> PublishResult:
    """Publish a reviewed model and compile it to Cube.

    Both steps must succeed. Publishing a graph that fails validation, or that
    Cube cannot compile, would produce a model that reports itself live while
    answering nothing — so those are 422s, not warnings in a log file.
    """
    pinned = graph.model_copy(update={"source_id": name})
    report = validate_graph(pinned, await _catalog(name))
    if not report.ok:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"{len(report.errors)} problem(s) must be fixed before publishing.",
                "issues": [i.model_dump() for i in report.errors],
            },
        )

    try:
        path = write_cube_model(pinned, get_settings().cube_model_dir)
    except (CubeCompileError, OSError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Could not compile the model for Cube: {exc}"
        ) from None

    result = await _service().publish(pinned)
    log.info("semantic.published", source=name, version=result.version, cube=path)
    return result


# ----------------------------------------------------------------------
# Business context — what the AI needs to know about this business
# ----------------------------------------------------------------------


@router.get("/context", response_model=BusinessContext)
async def get_context(request: Request, name: str) -> BusinessContext:
    repo = _contexts(request)
    stored = await repo.get(name)  # type: ignore[attr-defined]
    return stored or BusinessContext(source_id=name)


@router.put("/context", response_model=BusinessContext)
async def put_context(request: Request, name: str, context: BusinessContext) -> BusinessContext:
    repo = _contexts(request)
    return await repo.save(context.model_copy(update={"source_id": name}))  # type: ignore[attr-defined]


# ----------------------------------------------------------------------
# Build jobs
# ----------------------------------------------------------------------


@router.post("/generate", response_model=GenerationJob)
async def start_generate(
    request: Request,
    name: str,
    use_ai: bool = True,
    keep_edits: bool = True,
    tables: str | None = None,
) -> GenerationJob:
    """Start a background build: profile the schema, draft it, and (when AI is
    configured and ``use_ai``) enrich it in batches. Returns a job to poll.

    ``tables`` is a comma-separated scope — a 124-table database produces a model
    nobody can review, so the caller picks what matters. ``keep_edits`` folds the
    result onto the existing draft instead of discarding reviewed work.
    """
    _service()  # 503 if the app DB is down
    _source(name)  # 404 if the data source is unknown
    scope = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
    ai = use_ai and _ai_provider() is not None
    return _jobs(request).start_generate(name, ai, tables=scope, keep_edits=keep_edits)


@router.get("/job", response_model=GenerationJob | None)
async def active_job(request: Request, name: str) -> GenerationJob | None:
    """The build job currently running for this source, or null — lets the client
    resume watching after navigating away."""
    return _jobs(request).active_for(name)


@router.get("/jobs/{job_id}", response_model=GenerationJob)
async def get_job(request: Request, name: str, job_id: str) -> GenerationJob:
    job = _jobs(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id!r}")
    return job


# ----------------------------------------------------------------------
# Metric authoring
# ----------------------------------------------------------------------


@router.post("/entities/draft", response_model=EntityDraftResponse)
async def draft_entity(
    request: Request, name: str, body: EntityDraftRequest
) -> EntityDraftResponse:
    """Describe one entity in words; get its business name and description back.

    Text only — an entity's table, columns and key come from the database and
    are never up for negotiation. Nothing is saved: the editor fills its fields
    and the user presses Save.
    """
    provider = _require_ai()
    graph = await _load_draft(name)
    context = await _context_for(request, name)
    try:
        return await EntityDrafter(provider).draft(body, graph, context=context)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/metrics/draft", response_model=MetricDraftResponse)
async def draft_metric(
    request: Request, name: str, body: MetricDraftRequest
) -> MetricDraftResponse:
    """Describe a metric in words; get a filled-in definition back.

    Nothing is saved: the client fills its form with the result, the user checks
    it (ideally with ``/metrics/preview``) and presses Save. Every field is
    validated against the real catalog first, so an invented column comes back
    blank with a warning rather than as a plausible-looking mistake.
    """
    provider = _require_ai()
    graph = await _load_draft(name)
    context = await _context_for(request, name)
    try:
        return await MetricDrafter(provider).draft(body, graph, context=context)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/relationships/suggest", response_model=list[Relationship])
async def suggest_links(name: str) -> list[Relationship]:
    """Joins implied by column names that the model does not already have.

    Rule-based, not a model call: a wrong join silently pairs unrelated rows, so
    the answer has to be checkable. Ambiguous names are skipped rather than
    guessed, and nothing is saved — the user accepts what looks right.
    """
    graph = await _load_draft(name)
    return suggest_relationships(graph)


@router.post("/metrics/suggest", response_model=MetricSuggestResponse)
async def suggest_metrics(
    request: Request, name: str, body: MetricSuggestRequest
) -> MetricSuggestResponse:
    """Propose the metrics worth tracking on one entity.

    Scoped to one entity because most tables in a large schema are lookups
    nobody measures. Nothing is saved: the user picks which proposals to keep,
    and each one has already been checked against the real columns.
    """
    provider = _require_ai()
    graph = await _load_draft(name)
    context = await _context_for(request, name)
    try:
        return await MetricSuggester(provider).suggest(body, graph, context=context)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/metrics/preview", response_model=MetricPreview)
async def preview(name: str, metric: MetricDefinition) -> MetricPreview:
    """Run one metric against the real database and return the number.

    This is what makes a definition checkable by someone who knows the business
    but not SQL — and it works on an unsaved draft, which is exactly when the
    question "is this right?" needs answering.
    """
    graph = await _load_draft(name)
    source = _source(name)
    return await preview_metric(metric, graph, source, kind=source.dialect)
