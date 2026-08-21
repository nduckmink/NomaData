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

-- Saving a draft updates its row in place (revision +1) instead of inserting a
-- new version, so a model doesn't reach v9 before it has ever been published.
ALTER TABLE semantic_models ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE semantic_models ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
-- Older builds inserted one draft row per save. Collapse them to the newest
-- before the one-draft-per-source rule can be enforced.
DELETE FROM semantic_models a USING semantic_models b
 WHERE a.status = 'draft' AND b.status = 'draft'
   AND a.source_id = b.source_id AND a.version < b.version;
-- At most one open draft per source; published versions are immutable snapshots.
CREATE UNIQUE INDEX IF NOT EXISTS ux_semantic_models_draft
    ON semantic_models (source_id) WHERE status = 'draft';

-- What the AI needs to know about this business before it can name anything.
CREATE TABLE IF NOT EXISTS semantic_contexts (
    source_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL DEFAULT '',
    glossary TEXT NOT NULL DEFAULT '',
    conventions TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'en',
    instructions TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The zone this source's timestamps are read in; decides what "this month" means.
ALTER TABLE semantic_contexts ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'UTC';

-- One thread of questions against one source. A conversation exists so that
-- "and last month?" has something to refer to; keeping it also makes an answer
-- checkable later, which a number in a chat window otherwise never is.
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_conversations_source
    ON conversations (source_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'answer',
    question TEXT NOT NULL,
    query JSONB,
    result JSONB,
    answer TEXT NOT NULL DEFAULT '',
    explanation TEXT NOT NULL DEFAULT '',
    notes JSONB,
    -- Which published model answered. Once the model reaches v4, an answer from
    -- v3 cannot be reproduced — and the UI has to say so rather than let the
    -- number stand as if it were still current.
    model_version INTEGER,
    usage JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, ordinal)
);
-- What the agent did on the way to the answer. Kept with the turn so reopening
-- a thread shows how a number was reached, not only what it was.
ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS steps JSONB;

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
