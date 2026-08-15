"""AI provider config — manager logic (fake repo) and endpoints (hermetic)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from nomadata.ai_provider_manager import AIProviderManager
from nomadata.config import get_settings
from nomadata.core.models import AIProviderConfig
from nomadata.core.registry import Registry
from nomadata.main import app

client = TestClient(app)


class _FakeRepo:
    """In-memory stand-in for AIConfigRepository (single row)."""

    def __init__(self) -> None:
        self.cfg: AIProviderConfig | None = None

    async def get(self) -> AIProviderConfig | None:
        return self.cfg

    async def upsert(self, cfg: AIProviderConfig) -> None:
        self.cfg = cfg

    async def delete(self) -> bool:
        had = self.cfg is not None
        self.cfg = None
        return had


def _manager(repo: _FakeRepo, registry: Registry) -> AIProviderManager:
    return AIProviderManager(repo, registry, get_settings())  # type: ignore[arg-type]


# ---- manager ----


async def test_save_with_key_activates_provider() -> None:
    repo, registry = _FakeRepo(), Registry()
    manager = _manager(repo, registry)
    info = await manager.save(AIProviderConfig(api_key="secret", model="m"))
    assert info.configured
    assert registry.active_provider() is not None
    await manager.close()
    assert registry.active_provider() is None


async def test_save_without_key_stays_inactive() -> None:
    repo, registry = _FakeRepo(), Registry()
    manager = _manager(repo, registry)
    info = await manager.save(AIProviderConfig(api_key="", model="m"))
    assert info.configured is False
    assert registry.active_provider() is None


async def test_blank_key_reuses_stored_secret() -> None:
    repo, registry = _FakeRepo(), Registry()
    manager = _manager(repo, registry)
    await manager.save(AIProviderConfig(api_key="secret", model="m"))
    # A later save that leaves the key blank must keep the provider active.
    await manager.save(AIProviderConfig(api_key="", model="m2"))
    assert repo.cfg is not None
    assert repo.cfg.api_key == "secret"
    assert repo.cfg.model == "m2"
    assert registry.active_provider() is not None
    await manager.close()


async def test_get_info_never_exposes_key() -> None:
    repo, registry = _FakeRepo(), Registry()
    manager = _manager(repo, registry)
    await manager.save(AIProviderConfig(api_key="sk-abcdefghijklmnop", model="m"))
    info = await manager.get_info()
    assert info is not None
    dumped = info.model_dump()
    assert "api_key" not in dumped
    assert dumped["configured"] is True
    # Hint reveals only first 5 + last 3, never the full secret.
    assert dumped["key_hint"] == "sk-ab" + "•" * 8 + "nop"
    assert "sk-abcdefghijklmnop" not in dumped["key_hint"]


async def test_short_key_is_fully_masked() -> None:
    repo, registry = _FakeRepo(), Registry()
    manager = _manager(repo, registry)
    await manager.save(AIProviderConfig(api_key="short", model="m"))
    info = await manager.get_info()
    assert info is not None
    assert info.key_hint == "•" * 8  # too short to reveal any characters


async def test_clear_deactivates() -> None:
    repo, registry = _FakeRepo(), Registry()
    manager = _manager(repo, registry)
    await manager.save(AIProviderConfig(api_key="secret", model="m"))
    assert await manager.clear() is True
    assert registry.active_provider() is None
    assert await manager.get_info() is None


async def test_load_seeds_from_env_when_empty() -> None:
    repo, registry = _FakeRepo(), Registry()
    settings = get_settings().model_copy(
        update={"ai_api_key": "env-key", "ai_model": "env-model"}
    )
    manager = AIProviderManager(repo, registry, settings)  # type: ignore[arg-type]
    info = await manager.load()
    assert info is not None and info.configured
    assert repo.cfg is not None and repo.cfg.api_key == "env-key"
    assert registry.active_provider() is not None
    await manager.close()


# ---- endpoints (hermetic: app DB disabled in tests → 503) ----


def test_get_ai_config_unavailable_without_app_db() -> None:
    assert client.get("/api/v1/ai/config").status_code == 503


def test_put_ai_config_unavailable_without_app_db() -> None:
    resp = client.put("/api/v1/ai/config", json={"model": "m", "api_key": "k"})
    assert resp.status_code == 503
