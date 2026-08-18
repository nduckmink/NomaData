"""Repository for per-source business context.

One row per data source, holding what a newcomer would have to be told before
they could name anything in this database: what the business does, what the
local abbreviations mean, which language to write in. It is injected into every
semantic prompt (see ``semantic/prompt.py``).
"""

from __future__ import annotations

from typing import Any

from nomadata.core.models import BusinessContext
from nomadata.storage.database import Database

_COLUMNS = "source_id, domain, glossary, conventions, language, instructions"


def _to_context(row: Any) -> BusinessContext | None:
    if row is None:
        return None
    return BusinessContext(
        source_id=row["source_id"],
        domain=row["domain"],
        glossary=row["glossary"],
        conventions=row["conventions"],
        language=row["language"],
        instructions=row["instructions"],
    )


class BusinessContextRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, source_id: str) -> BusinessContext | None:
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM semantic_contexts WHERE source_id=$1", source_id
            )
        return _to_context(row)

    async def save(self, context: BusinessContext) -> BusinessContext:
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO semantic_contexts "
                f"({_COLUMNS}) VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (source_id) DO UPDATE SET "
                "domain=EXCLUDED.domain, glossary=EXCLUDED.glossary, "
                "conventions=EXCLUDED.conventions, language=EXCLUDED.language, "
                "instructions=EXCLUDED.instructions, updated_at=now() "
                f"RETURNING {_COLUMNS}",
                context.source_id,
                context.domain,
                context.glossary,
                context.conventions,
                context.language,
                context.instructions,
            )
        saved = _to_context(row)
        assert saved is not None  # RETURNING always yields a row
        return saved

    async def delete(self, source_id: str) -> None:
        async with self._db.pool.acquire() as conn:
            await conn.execute("DELETE FROM semantic_contexts WHERE source_id=$1", source_id)
