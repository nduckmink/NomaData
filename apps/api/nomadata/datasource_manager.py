"""Data source lifecycle — persist connections and (un)register live connectors.

App-layer glue that ties together the repository (persistence), the connector
factory (builds a live DataSource), and the registry (what the API resolves).
Adding/updating/removing a source takes effect immediately — no restart.
"""

from __future__ import annotations

from nomadata.connectors import build_data_source
from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.models import DataSourceConfig
from nomadata.core.registry import Registry
from nomadata.storage.data_source_repo import DataSourceRepository


class DataSourceManager:
    def __init__(self, repo: DataSourceRepository, registry: Registry) -> None:
        self._repo = repo
        self._registry = registry

    def _build(self, cfg: DataSourceConfig) -> DataSource:
        return build_data_source(
            cfg.kind,
            name=cfg.name,
            host=cfg.host,
            port=cfg.port,
            database=cfg.database,
            user=cfg.user,
            password=cfg.resolve_password(),
        )

    async def load_all(self) -> int:
        """Register every persisted data source. Returns the count."""
        configs = await self._repo.list()
        for cfg in configs:
            self._registry.register_data_source(cfg.name, self._build(cfg))
        return len(configs)

    async def create(self, cfg: DataSourceConfig) -> DataSourceConfig:
        await self._repo.create(cfg)  # raises DataSourceExistsError on conflict
        self._registry.register_data_source(cfg.name, self._build(cfg))
        return cfg

    async def update(self, name: str, cfg: DataSourceConfig) -> bool:
        cfg = cfg.model_copy(update={"name": name})
        updated = await self._repo.update(cfg)
        if not updated:
            return False
        await self._close_and_unregister(name)
        self._registry.register_data_source(cfg.name, self._build(cfg))
        return True

    async def delete(self, name: str) -> bool:
        deleted = await self._repo.delete(name)
        if deleted:
            await self._close_and_unregister(name)
        return deleted

    async def close_all(self) -> None:
        for name in self._registry.data_source_names():
            await self._close(self._registry.get_data_source(name))

    async def _close_and_unregister(self, name: str) -> None:
        old = self._registry.unregister_data_source(name)
        if old is not None:
            await self._close(old)

    @staticmethod
    async def _close(source: DataSource) -> None:
        close = getattr(source, "close", None)
        if close is not None:
            await close()
