"""AI provider configuration endpoints — view, save, test, clear.

Config is persisted in the app DB and the live provider is (re)registered on
save, so changes take effect without a restart. The API key is never returned;
responses use the safe ``AIProviderInfo`` view.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from nomadata.ai_provider_manager import AIProviderManager
from nomadata.core.models import AIProviderConfig, AIProviderInfo, ConnectionStatus

router = APIRouter(prefix="/ai", tags=["ai"])


def _manager(request: Request) -> AIProviderManager:
    manager = getattr(request.app.state, "ai_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="AI configuration unavailable — app database not connected.",
        )
    return manager


@router.get("/config", response_model=AIProviderInfo | None)
async def get_ai_config(request: Request) -> AIProviderInfo | None:
    """Current AI config (safe view, no key), or null if unconfigured."""
    return await _manager(request).get_info()


@router.put("/config", response_model=AIProviderInfo)
async def put_ai_config(request: Request, config: AIProviderConfig) -> AIProviderInfo:
    """Save config and register the live provider. A blank ``api_key`` reuses the
    stored secret (so the form need not re-send it)."""
    return await _manager(request).save(config)


@router.post("/config/test", response_model=ConnectionStatus)
async def test_ai_config(request: Request, config: AIProviderConfig) -> ConnectionStatus:
    """Test an AI config WITHOUT saving it (used by the settings form)."""
    return await _manager(request).verify(config)


@router.delete("/config", status_code=204)
async def delete_ai_config(request: Request) -> Response:
    await _manager(request).clear()
    return Response(status_code=204)
