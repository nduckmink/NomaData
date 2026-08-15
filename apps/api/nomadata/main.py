"""FastAPI application factory and composition root.

This is the only module allowed to import across every layer. In later phases it
will register concrete AIProvider / DataSource / QueryEngine implementations into
the registry based on configuration. In M0 the registries are wired but empty.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nomadata.api.v1.router import router as v1_router
from nomadata.config import get_settings
from nomadata.connectors import build_data_source
from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.registry import get_registry
from nomadata.data_sources import load_data_sources
from nomadata.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger()
    log.info("nomadata.startup", env=settings.env, version=settings.version)

    # M1: register data sources declared in data_sources.json.
    registry = get_registry()
    sources: list[DataSource] = []
    for cfg in load_data_sources(settings.data_sources_file or None):
        source = build_data_source(
            cfg.kind,
            name=cfg.name,
            host=cfg.host,
            port=cfg.port,
            database=cfg.database,
            user=cfg.user,
            password=cfg.resolve_password(),
        )
        registry.register_data_source(source.name, source)
        sources.append(source)
        log.info("nomadata.datasource.registered", name=source.name, kind=cfg.kind)

    yield

    for source in sources:
        close = getattr(source, "close", None)
        if close is not None:
            await close()
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
    app.include_router(v1_router, prefix="/api/v1")
    return app


app = create_app()
