"""Repository for the AI provider configuration (single active row).

The API key is stored plaintext for now (dev, like data source passwords); use
``api_key_env`` to keep it in the environment instead. Encryption at rest lands
in Phase 6.
"""

from __future__ import annotations

from typing import Any

from nomadata.core.models import AIProviderConfig
from nomadata.storage.database import Database


def _row_to_config(row: Any) -> AIProviderConfig:
    return AIProviderConfig(
        provider=row["provider"],
        base_url=row["base_url"],
        api_key=row["api_key"],
        api_key_env=row["api_key_env"],
        model=row["model"],
    )


class AIConfigRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self) -> AIProviderConfig | None:
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT provider, base_url, api_key, api_key_env, model FROM ai_config WHERE id = 1"
            )
        return _row_to_config(row) if row else None

    async def upsert(self, cfg: AIProviderConfig) -> None:
        async with self._db.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ai_config (id, provider, base_url, api_key, api_key_env, model) "
                "VALUES (1, $1, $2, $3, $4, $5) "
                "ON CONFLICT (id) DO UPDATE SET "
                "provider=$1, base_url=$2, api_key=$3, api_key_env=$4, model=$5, updated_at=now()",
                cfg.provider,
                cfg.base_url,
                cfg.api_key,
                cfg.api_key_env,
                cfg.model,
            )

    async def delete(self) -> bool:
        async with self._db.pool.acquire() as conn:
            status = await conn.execute("DELETE FROM ai_config WHERE id = 1")
        return status.endswith(" 1")
