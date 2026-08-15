"""App PostgreSQL pool + schema bootstrap.

M2 uses ``CREATE TABLE IF NOT EXISTS`` for simplicity; real migrations (Alembic)
come before multi-environment production.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from nomadata.core.errors import ConfigurationError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_sources (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    database TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    password TEXT NOT NULL DEFAULT '',
    password_env TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS semantic_models (
    id BIGSERIAL PRIMARY KEY,
    source_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    graph JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, version)
);
CREATE INDEX IF NOT EXISTS ix_semantic_models_source
    ON semantic_models (source_id, version DESC);

CREATE TABLE IF NOT EXISTS ai_config (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    provider TEXT NOT NULL DEFAULT 'openai_compatible',
    base_url TEXT NOT NULL DEFAULT '',
    api_key TEXT NOT NULL DEFAULT '',
    api_key_env TEXT,
    model TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=5, timeout=10)
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> Any:
        if self._pool is None:
            raise ConfigurationError("App database pool is not connected.")
        return self._pool
