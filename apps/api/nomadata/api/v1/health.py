"""Health / system status endpoint.

M0's single user-visible outcome: the web client calls this and renders a
"System OK" status page. Reports registry state (empty in M0 but wired).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from nomadata.config import get_settings
from nomadata.core.registry import get_registry

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    version: str
    env: str
    checks: dict[str, str]
    providers: list[str]
    data_sources: list[str]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    registry = get_registry()
    return HealthResponse(
        status="ok",
        version=settings.version,
        env=settings.env,
        checks={"api": "ok"},
        providers=registry.provider_names(),
        data_sources=registry.data_source_names(),
    )
