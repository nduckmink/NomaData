"""API v1 router — mounts all v1 endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from nomadata.api.v1 import datasources, health

router = APIRouter()
router.include_router(health.router)
router.include_router(datasources.router)
