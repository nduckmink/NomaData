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
from nomadata.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger()
    log.info("nomadata.startup", env=settings.env, version=settings.version)
    # M0: no providers/connectors registered yet. Later phases wire them here.
    yield
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
