"""Repository for semantic model artifacts — versioned, stored as JSONB."""

from __future__ import annotations

import json
from typing import Any

from nomadata.core.models import PublishResult, SemanticGraph, SemanticModelVersion
from nomadata.storage.database import Database


def _to_graph(row: Any) -> SemanticGraph | None:
    if row is None:
        return None
    data = row["graph"]
    if isinstance(data, str):  # asyncpg returns JSONB as text by default
        data = json.loads(data)
    return SemanticGraph.model_validate(data)


class SemanticRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def _next_version(self, conn: Any, source_id: str) -> int:
        row = await conn.fetchrow(
            "SELECT COALESCE(MAX(version), 0) AS v FROM semantic_models WHERE source_id=$1",
            source_id,
        )
        return int(row["v"]) + 1

    async def _save(self, graph: SemanticGraph, *, status: str) -> SemanticGraph:
        async with self._db.pool.acquire() as conn, conn.transaction():
            version = await self._next_version(conn, graph.source_id)
            stored = graph.model_copy(
                update={"version": version, "published": status == "published"}
            )
            await conn.execute(
                "INSERT INTO semantic_models (source_id, version, status, graph) "
                "VALUES ($1, $2, $3, $4::jsonb)",
                stored.source_id,
                version,
                status,
                stored.model_dump_json(),
            )
            return stored

    async def save_draft(self, graph: SemanticGraph) -> SemanticGraph:
        return await self._save(graph, status="draft")

    async def publish(self, graph: SemanticGraph) -> PublishResult:
        stored = await self._save(graph, status="published")
        return PublishResult(source_id=stored.source_id, version=stored.version, published=True)

    async def get_latest(self, source_id: str) -> SemanticGraph | None:
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT graph FROM semantic_models WHERE source_id=$1 "
                "ORDER BY version DESC LIMIT 1",
                source_id,
            )
        return _to_graph(row)

    async def get_published(self, source_id: str) -> SemanticGraph | None:
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT graph FROM semantic_models "
                "WHERE source_id=$1 AND status='published' "
                "ORDER BY version DESC LIMIT 1",
                source_id,
            )
        return _to_graph(row)

    async def delete(self, source_id: str) -> int:
        """Delete every version for a source. Returns the number of rows removed."""
        async with self._db.pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM semantic_models WHERE source_id=$1", source_id
            )
        # asyncpg returns a tag like "DELETE 3".
        return int(status.split()[-1])

    async def list_versions(self, source_id: str) -> list[SemanticModelVersion]:
        async with self._db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT version, status, created_at FROM semantic_models "
                "WHERE source_id=$1 ORDER BY version DESC",
                source_id,
            )
        return [
            SemanticModelVersion(
                version=r["version"],
                status=r["status"],
                created_at=r["created_at"].isoformat(),
            )
            for r in rows
        ]
