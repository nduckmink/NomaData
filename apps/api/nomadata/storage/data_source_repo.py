"""Repository for data source connections stored in the app DB.

Passwords are stored plaintext for now (dev); use ``password_env`` to keep the
secret in the environment instead. Encryption at rest lands in Phase 6.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from nomadata.core.errors import NomaDataError
from nomadata.core.models import DataSourceConfig
from nomadata.storage.database import Database


class DataSourceExistsError(NomaDataError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Data source already exists: {name!r}")
        self.name = name


def _row_to_config(row: Any) -> DataSourceConfig:
    return DataSourceConfig(
        name=row["name"],
        kind=row["kind"],
        host=row["host"],
        port=row["port"],
        database=row["database"],
        user=row["username"],
        password=row["password"],
        password_env=row["password_env"],
    )


class DataSourceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self) -> list[DataSourceConfig]:
        async with self._db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT name, kind, host, port, database, username, password, "
                "password_env FROM data_sources ORDER BY name"
            )
        return [_row_to_config(r) for r in rows]

    async def get(self, name: str) -> DataSourceConfig | None:
        async with self._db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name, kind, host, port, database, username, password, "
                "password_env FROM data_sources WHERE name=$1",
                name,
            )
        return _row_to_config(row) if row else None

    async def create(self, cfg: DataSourceConfig) -> None:
        try:
            async with self._db.pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO data_sources "
                    "(name, kind, host, port, database, username, password, password_env) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                    cfg.name,
                    cfg.kind,
                    cfg.host,
                    cfg.port,
                    cfg.database,
                    cfg.user,
                    cfg.password,
                    cfg.password_env,
                )
        except asyncpg.UniqueViolationError as exc:
            raise DataSourceExistsError(cfg.name) from exc

    async def update(self, cfg: DataSourceConfig) -> bool:
        async with self._db.pool.acquire() as conn:
            status = await conn.execute(
                "UPDATE data_sources SET kind=$2, host=$3, port=$4, database=$5, "
                "username=$6, password=$7, password_env=$8, updated_at=now() "
                "WHERE name=$1",
                cfg.name,
                cfg.kind,
                cfg.host,
                cfg.port,
                cfg.database,
                cfg.user,
                cfg.password,
                cfg.password_env,
            )
        return status.endswith(" 1")

    async def delete(self, name: str) -> bool:
        async with self._db.pool.acquire() as conn:
            status = await conn.execute("DELETE FROM data_sources WHERE name=$1", name)
        return status.endswith(" 1")
