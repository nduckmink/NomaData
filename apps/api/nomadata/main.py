"""FastAPI application factory and composition root.

This is the only module allowed to import across every layer. In later phases it
will register concrete AIProvider / DataSource / QueryEngine implementations into
the registry based on configuration. In M0 the registries are wired but empty.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from nomadata.ai_provider_manager import AIProviderManager
from nomadata.api.v1.router import router as v1_router
from nomadata.config import get_settings
from nomadata.core.errors import DataConnectionError
from nomadata.core.registry import get_registry
from nomadata.datasource_manager import DataSourceManager
from nomadata.logging import configure_logging, get_logger
from nomadata.query.cube import CubeQueryEngine
from nomadata.semantic.jobs import SemanticJobRunner
from nomadata.semantic.service import SemanticModelService
from nomadata.storage.ai_config_repo import AIConfigRepository
from nomadata.storage.context_repo import BusinessContextRepository
from nomadata.storage.data_source_repo import DataSourceRepository
from nomadata.storage.database import Database
from nomadata.storage.semantic_repo import SemanticRepository


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger()
    log.info("nomadata.startup", env=settings.env, version=settings.version)

    # Data sources and semantic models live in the app DB. Connect it, register
    # the semantic service, and load persisted data source connections. Degrade
    # gracefully if the app DB is unavailable — the API still serves /health.
    registry = get_registry()
    # Cube query engine — holds config only; run() fails cleanly if Cube is down.
    registry.set_query_engine(CubeQueryEngine(settings.cube_url, settings.cube_api_secret))
    database: Database | None = None
    manager: DataSourceManager | None = None
    ai_manager: AIProviderManager | None = None
    if settings.database_url:
        database = Database(settings.database_url)
        try:
            await database.connect()
            semantic_service = SemanticModelService(SemanticRepository(database))
            registry.set_semantic_model(semantic_service)
            contexts = BusinessContextRepository(database)
            app.state.semantic_contexts = contexts
            app.state.semantic_jobs = SemanticJobRunner(registry, semantic_service, contexts)
            manager = DataSourceManager(DataSourceRepository(database), registry)
            count = await manager.load_all()
            app.state.datasource_manager = manager
            # AI provider config lives in the app DB (editable in the UI). Without
            # a key the semantic suggester falls back to its heuristic baseline.
            ai_manager = AIProviderManager(AIConfigRepository(database), registry, settings)
            ai_info = await ai_manager.load()
            app.state.ai_manager = ai_manager
            log.info(
                "nomadata.appdb.connected",
                data_sources=count,
                ai_configured=ai_info is not None and ai_info.configured,
            )
        except Exception as exc:  # noqa: BLE001 - degrade, don't crash the API
            log.warning("nomadata.appdb.unavailable", error=str(exc))
            database = None
            manager = None
            ai_manager = None

    yield

    if manager is not None:
        await manager.close_all()
    if ai_manager is not None:
        await ai_manager.close()
    if database is not None:
        await database.close()
    log.info("nomadata.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="NomaData API",
        version=settings.version,
        summary="Model-agnostic AI client for conversational BI.",
        lifespan=lifespan,
    )
    # In development, accept any localhost/127.0.0.1 port so the client keeps
    # working when Next.js falls back to :3001 etc. (port 3000 already in use).
    # Production uses the explicit allowlist only.
    if settings.env == "development":
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(DataConnectionError)
    async def _data_connection_error(_: Request, exc: DataConnectionError) -> JSONResponse:
        # A data source that can't be reached is an upstream failure, not a bug.
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    app.include_router(v1_router, prefix="/api/v1")
    return app


app = create_app()
