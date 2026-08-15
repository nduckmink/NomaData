"""AI provider lifecycle — persist config and (re)register the live provider.

App-layer glue tying the repository (persistence), the provider factory (builds
a live ``AIProvider``), and the registry (what the suggester resolves). Saving
config takes effect immediately — no restart. Mirrors ``DataSourceManager``.
"""

from __future__ import annotations

from nomadata.config import Settings
from nomadata.core.models import AIProviderConfig, AIProviderInfo, ConnectionStatus
from nomadata.core.registry import Registry
from nomadata.logging import get_logger
from nomadata.providers.openai_compatible import OpenAICompatibleProvider
from nomadata.storage.ai_config_repo import AIConfigRepository

_log = get_logger()


def _build(cfg: AIProviderConfig) -> OpenAICompatibleProvider:
    # Only one provider kind today; it speaks the OpenAI-compatible wire format,
    # which most endpoints (OpenAI, OpenRouter, DeepSeek, local) implement.
    return OpenAICompatibleProvider(
        name=cfg.provider,
        base_url=cfg.base_url,
        api_key=cfg.resolve_api_key(),
        model=cfg.model,
    )


class AIProviderManager:
    def __init__(
        self, repo: AIConfigRepository, registry: Registry, settings: Settings
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._settings = settings
        self._provider: OpenAICompatibleProvider | None = None

    async def load(self) -> AIProviderInfo | None:
        """Register the persisted AI provider on startup. If nothing is stored
        yet but a key sits in the environment (legacy ``.env`` config), seed the
        DB from it once so existing setups keep working."""
        cfg = await self._repo.get()
        if cfg is None and self._settings.ai_api_key:
            cfg = AIProviderConfig(
                provider=self._settings.ai_provider,
                base_url=self._settings.ai_base_url,
                api_key=self._settings.ai_api_key,
                model=self._settings.ai_model,
            )
            await self._repo.upsert(cfg)
            _log.info("nomadata.ai.seeded_from_env")
        if cfg is None:
            return None
        await self._activate(cfg)
        return cfg.to_info()

    async def get_info(self) -> AIProviderInfo | None:
        cfg = await self._repo.get()
        return cfg.to_info() if cfg else None

    async def resolve_config(self, cfg: AIProviderConfig) -> AIProviderConfig:
        """Fill a blank key from the stored config (so 'edit' / 'test' don't need
        the secret re-typed)."""
        if not cfg.api_key and not cfg.api_key_env:
            existing = await self._repo.get()
            if existing is not None:
                return cfg.model_copy(
                    update={"api_key": existing.api_key, "api_key_env": existing.api_key_env}
                )
        return cfg

    async def save(self, cfg: AIProviderConfig) -> AIProviderInfo:
        cfg = await self.resolve_config(cfg)
        await self._repo.upsert(cfg)
        await self._activate(cfg)
        return cfg.to_info()

    async def verify(self, cfg: AIProviderConfig) -> ConnectionStatus:
        cfg = await self.resolve_config(cfg)
        provider = _build(cfg)
        try:
            return await provider.verify()
        finally:
            await provider.aclose()

    async def clear(self) -> bool:
        deleted = await self._repo.delete()
        await self._deactivate()
        return deleted

    async def close(self) -> None:
        await self._deactivate()

    async def _activate(self, cfg: AIProviderConfig) -> None:
        await self._deactivate()
        # No usable key → treat as unconfigured rather than a broken provider.
        if not cfg.resolve_api_key():
            _log.info("nomadata.ai.no_key", provider=cfg.provider)
            return
        self._provider = _build(cfg)
        self._registry.set_active_provider(self._provider)
        _log.info("nomadata.ai.active", provider=cfg.provider, model=cfg.model)

    async def _deactivate(self) -> None:
        self._registry.set_active_provider(None)
        if self._provider is not None:
            await self._provider.aclose()
            self._provider = None
