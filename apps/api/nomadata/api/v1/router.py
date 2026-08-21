"""API v1 router — mounts all v1 endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from nomadata.api.v1 import (
    ai,
    chat,
    conversations,
    datasources,
    health,
    query,
    semantic,
    semantic_overview,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(datasources.router)
router.include_router(semantic.router)
router.include_router(semantic_overview.router)
router.include_router(query.router)
router.include_router(ai.router)
router.include_router(chat.router)
router.include_router(conversations.router)
