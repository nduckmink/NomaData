"""Repository for semantic model artifacts — versioned, stored as JSONB.

Version numbers count *publishes*, not saves. A source has at most one open
draft row, updated in place; publishing turns that row into an immutable
snapshot and the next edit opens a fresh draft. Without this a model reached v9
before anyone had ever published it, which made the version number meaningless.

Concurrent edits are caught with a revision counter rather than last-write-wins:
two tabs on the same draft collide loudly instead of one silently losing.
"""

from __future__ import annotations

import json
from typing import Any

from nomadata.core.errors import NomaDataError
from nomadata.core.models import PublishResult, SemanticGraph, SemanticModelVersion
from nomadata.storage.database import Database


class SemanticConflictError(NomaDataError):
    """The draft changed underneath this write (another tab, another session)."""

    def __init__(self, source_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Semantic model for {source_id!r} was modified elsewhere "
            f"(expected revision {expected}, found {actual}). Reload before saving."
        )
        self.source_id = source_id
        self.expected = expected
        self.actual = actual


def _jsonb(graph: SemanticGraph) -> str:
    """Serialize a graph for a ``jsonb`` column.

    PostgreSQL refuses ``\\u0000`` in ``jsonb`` — a single NUL byte anywhere in
    the graph fails the whole write with *"unsupported Unicode escape
    sequence"*. Connectors already strip NUL from the values they read, but a
    graph can also arrive from the client or from a model response, and losing
    an entire build to one stray byte is not a reasonable failure mode.
    """
    return graph.model_dump_json().replace("\\u0000", "")


def _to_graph(row: Any) -> SemanticGraph | None:
    if row is None:
        return None
    data = row["graph"]
    if isinstance(data, str):  # asyncpg returns JSONB as text by default
        data = json.loads(data)
    graph = SemanticGraph.model_validate(data)
    # The row is the authority on identity, not the stored blob.
    return graph.model_copy(
        update={
            "version": row["version"],
            "revision": row["revision"],
            "published": row["status"] == "published",
        }
    )


_SELECT = "SELECT graph, version, revision, status FROM semantic_models"


class SemanticRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_draft(
        self, graph: SemanticGraph, *, expected_revision: int | None = None
    ) -> SemanticGraph:
        """Create or update the single open draft for this source.

        ``expected_revision`` makes the write conditional: pass what the client
        loaded and a concurrent change raises instead of overwriting.
        """
        async with self._db.pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchrow(
                "SELECT version, revision FROM semantic_models "
                "WHERE source_id=$1 AND status='draft'",
                graph.source_id,
            )
            if existing is None:
                row = await conn.fetchrow(
                    "SELECT COALESCE(MAX(version), 0) AS v FROM semantic_models WHERE source_id=$1",
                    graph.source_id,
                )
                version = int(row["v"]) + 1
                stored = graph.model_copy(
                    update={"version": version, "revision": 1, "published": False}
                )
                await conn.execute(
                    "INSERT INTO semantic_models "
                    "(source_id, version, status, graph, revision) "
                    "VALUES ($1, $2, 'draft', $3::jsonb, 1)",
                    stored.source_id,
                    version,
                    _jsonb(stored),
                )
                return stored

            current = int(existing["revision"])
            if expected_revision is not None and expected_revision != current:
                raise SemanticConflictError(graph.source_id, expected_revision, current)

            revision = current + 1
            stored = graph.model_copy(
                update={
                    "version": int(existing["version"]),
                    "revision": revision,
                    "published": False,
                }
            )
            await conn.execute(
                "UPDATE semantic_models SET graph=$2::jsonb, revision=$3, "
                "updated_at=now() WHERE source_id=$1 AND status='draft'",
                stored.source_id,
                _jsonb(stored),
                revision,
            )
            return stored

    async def publish(self, graph: SemanticGraph) -> PublishResult:
        """Snapshot the given graph as a published version.

        The open draft becomes that snapshot, so publishing does not leave a
        stale draft claiming to be newer than what is live.

        Publishing is only ever meaningful when there is an open draft — that is
        the pending work. Once a model is published, editing it opens a fresh
        draft; so "no open draft" means nothing has changed since the live
        version, and re-publishing must NOT mint a new version (the UI disables
        the button, and this is the matching guard for any other caller).
        """
        async with self._db.pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchrow(
                "SELECT version FROM semantic_models WHERE source_id=$1 AND status='draft'",
                graph.source_id,
            )
            if existing is not None:
                version = int(existing["version"])
                stored = graph.model_copy(update={"version": version, "published": True})
                await conn.execute(
                    "UPDATE semantic_models SET graph=$2::jsonb, status='published', "
                    "updated_at=now() WHERE source_id=$1 AND status='draft'",
                    stored.source_id,
                    _jsonb(stored),
                )
                return PublishResult(source_id=stored.source_id, version=version, published=True)

            # No open draft: the newest thing is already published.
            published = await conn.fetchrow(
                "SELECT MAX(version) AS v FROM semantic_models "
                "WHERE source_id=$1 AND status='published'",
                graph.source_id,
            )
            if published is not None and published["v"] is not None:
                # Nothing to publish — return the live version unchanged.
                return PublishResult(
                    source_id=graph.source_id,
                    version=int(published["v"]),
                    published=True,
                )

            # Truly empty (never published, no draft) — a first publish. Rare via
            # the UI, which builds a draft first, but keep it correct.
            stored = graph.model_copy(update={"version": 1, "published": True})
            await conn.execute(
                "INSERT INTO semantic_models "
                "(source_id, version, status, graph, revision) "
                "VALUES ($1, 1, 'published', $2::jsonb, 1)",
                stored.source_id,
                _jsonb(stored),
            )
            return PublishResult(source_id=stored.source_id, version=1, published=True)

    async def get_latest(self, source_id: str) -> SemanticGraph | None:
        """The open draft if there is one, else the newest published version."""
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"{_SELECT} WHERE source_id=$1 "
                "ORDER BY (status='draft') DESC, version DESC LIMIT 1",
                source_id,
            )
        return _to_graph(row)

    async def get_published(self, source_id: str) -> SemanticGraph | None:
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"{_SELECT} WHERE source_id=$1 AND status='published' "
                "ORDER BY version DESC LIMIT 1",
                source_id,
            )
        return _to_graph(row)

    async def delete(self, source_id: str) -> int:
        """Delete every version for a source. Returns the number of rows removed."""
        async with self._db.pool.acquire() as conn:
            status = await conn.execute("DELETE FROM semantic_models WHERE source_id=$1", source_id)
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
