"""Repository for question threads and the turns inside them.

Two reasons a turn is written down, and only one of them is the chat window.
The first is follow-up: "and last month?" means nothing without the query it
amends. The second is accountability — a number that appeared in a chat window
and was never recorded cannot be checked afterwards, and the version of the
model that produced it will have moved on. So every turn is stored with the
`model_version` that answered it, including the ones that failed: a question the
agent could not answer is the most useful thing in the table.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from nomadata.core.models import (
    AgentTurn,
    AnalyticalQuery,
    Conversation,
    ConversationTurn,
    QueryResult,
    TurnUsage,
)
from nomadata.storage.database import Database

_TURN_COLUMNS = (
    "ordinal, kind, question, query, result, answer, explanation, notes, "
    "model_version, usage, error, created_at"
)


def _json(value: Any) -> str | None:
    """Serialise for JSONB, or ``None`` so the column stays null rather than 'null'."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(raw: Any) -> Any:
    return json.loads(raw) if isinstance(raw, str) else raw


def _to_turn(row: Any) -> ConversationTurn:
    query = _loads(row["query"])
    result = _loads(row["result"])
    usage = _loads(row["usage"]) or {}
    return ConversationTurn(
        ordinal=row["ordinal"],
        kind=row["kind"],
        question=row["question"],
        query=AnalyticalQuery.model_validate(query) if query else None,
        result=QueryResult.model_validate(result) if result else None,
        answer=row["answer"],
        explanation=row["explanation"],
        notes=_loads(row["notes"]) or [],
        model_version=row["model_version"],
        usage=TurnUsage.model_validate(usage),
        error=row["error"] or "",
        created_at=row["created_at"],
    )


class ConversationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def start(self, source_id: str, title: str = "") -> str:
        conversation_id = str(uuid.uuid4())
        async with self._db.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO conversations (id, source_id, title) VALUES ($1::uuid, $2, $3)",
                conversation_id,
                source_id,
                title[:200],
            )
        return conversation_id

    async def exists(self, conversation_id: str, source_id: str) -> bool:
        """A thread is only continuable on the source it was started against.

        Carrying one across sources would put a question next to history from a
        different model, where the metric names mean something else.
        """
        try:
            uuid.UUID(conversation_id)
        except ValueError:
            return False
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM conversations WHERE id=$1::uuid AND source_id=$2",
                conversation_id,
                source_id,
            )
        return row is not None

    async def append(self, conversation_id: str, turn: AgentTurn) -> int:
        """Store one turn and return its ordinal, counted from 1."""
        async with self._db.pool.acquire() as conn, conn.transaction():
            ordinal = await conn.fetchval(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM conversation_turns "
                "WHERE conversation_id=$1::uuid",
                conversation_id,
            )
            await conn.execute(
                "INSERT INTO conversation_turns (conversation_id, ordinal, kind, "
                "question, query, result, answer, explanation, notes, "
                "model_version, usage, error) VALUES "
                "($1::uuid, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9::jsonb, "
                "$10, $11::jsonb, $12)",
                conversation_id,
                ordinal,
                turn.kind,
                turn.question,
                _json(turn.query.model_dump(mode="json") if turn.query else None),
                _json(turn.result.model_dump(mode="json") if turn.result else None),
                # One column for what the user was shown, whatever the kind:
                # the headline, the question back, or the refusal. `error`
                # stays for the turns that genuinely broke.
                turn.answer or turn.clarification or turn.reason,
                turn.explanation,
                _json(turn.notes),
                turn.model_version,
                _json(turn.usage.model_dump(mode="json")),
                turn.reason if turn.kind == "error" else None,
            )
            # The first question names the thread — a title nobody has to
            # write is a title that exists.
            await conn.execute(
                "UPDATE conversations SET updated_at = now(), "
                "title = CASE WHEN title = '' THEN $2 ELSE title END "
                "WHERE id=$1::uuid",
                conversation_id,
                turn.question[:200],
            )
        return int(ordinal)

    async def recent_turns(self, conversation_id: str, limit: int = 5) -> list[ConversationTurn]:
        """The last ``limit`` turns, oldest first — what the next turn refers to."""
        async with self._db.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_TURN_COLUMNS} FROM conversation_turns "
                "WHERE conversation_id=$1::uuid ORDER BY ordinal DESC LIMIT $2",
                conversation_id,
                limit,
            )
        return [_to_turn(row) for row in reversed(rows)]

    async def get(self, conversation_id: str) -> Conversation | None:
        async with self._db.pool.acquire() as conn:
            head = await conn.fetchrow(
                "SELECT id, source_id, title, created_at, updated_at FROM conversations "
                "WHERE id=$1::uuid",
                conversation_id,
            )
            if head is None:
                return None
            rows = await conn.fetch(
                f"SELECT {_TURN_COLUMNS} FROM conversation_turns "
                "WHERE conversation_id=$1::uuid ORDER BY ordinal",
                conversation_id,
            )
        turns = [_to_turn(row) for row in rows]
        return Conversation(
            id=str(head["id"]),
            source_id=head["source_id"],
            title=head["title"],
            turns=turns,
            turn_count=len(turns),
            created_at=head["created_at"],
            updated_at=head["updated_at"],
        )

    async def list(self, source_id: str, limit: int = 50) -> list[Conversation]:
        """Threads for a source, most recently used first, without their turns."""
        async with self._db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT c.id, c.source_id, c.title, c.created_at, c.updated_at, "
                "COUNT(t.id) AS turn_count FROM conversations c "
                "LEFT JOIN conversation_turns t ON t.conversation_id = c.id "
                "WHERE c.source_id=$1 GROUP BY c.id ORDER BY c.updated_at DESC LIMIT $2",
                source_id,
                limit,
            )
        return [
            Conversation(
                id=str(row["id"]),
                source_id=row["source_id"],
                title=row["title"],
                turn_count=row["turn_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def delete(self, conversation_id: str) -> bool:
        async with self._db.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM conversations WHERE id=$1::uuid", conversation_id
            )
        return result.endswith("1")
