"""MySQL data source connector.

One of the only modules (alongside other connectors) allowed to import a
database driver. Implements the ``DataSource`` interface using aiomysql.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any

import aiomysql

from nomadata.core.errors import DataConnectionError
from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.models import (
    ColumnInfo,
    ColumnProfile,
    ConnectionState,
    ConnectionStatus,
    DatabaseCatalog,
    ExecutionPlan,
    ForeignKey,
    ProfileTarget,
    QueryResult,
    ResultColumn,
    TableInfo,
)


def _quote_ident(name: str) -> str:
    """Backtick-quote an identifier (prevents identifier injection)."""
    return "`" + name.replace("`", "``") + "`"


def _to_jsonable(value: Any) -> Any:
    """Coerce driver-native values into JSON-serializable primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


class MySQLDataSource(DataSource):
    def __init__(
        self,
        *,
        name: str,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
    ) -> None:
        self._name = name
        self._host = host
        self._port = port
        self._database = database
        self._user = user
        self._password = password
        self._pool: Any = None
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._name

    async def _get_pool(self) -> Any:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    try:
                        self._pool = await aiomysql.create_pool(
                            host=self._host,
                            port=self._port,
                            user=self._user,
                            password=self._password,
                            db=self._database,
                            autocommit=True,
                            minsize=1,
                            maxsize=5,
                        )
                    except Exception as exc:  # noqa: BLE001 - surfaced as domain error
                        raise DataConnectionError(
                            f"MySQL connection failed for {self._name!r}: {exc}"
                        ) from exc
        return self._pool

    async def _fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
            return list(rows)

    async def test_connection(self) -> ConnectionStatus:
        start = time.perf_counter()
        try:
            await self._fetch_all("SELECT 1")
        except Exception as exc:  # noqa: BLE001 - reported as status, not raised
            return ConnectionStatus(state=ConnectionState.error, message=str(exc))
        latency_ms = (time.perf_counter() - start) * 1000
        return ConnectionStatus(state=ConnectionState.ok, latency_ms=round(latency_ms, 2))

    async def inspect_schema(self) -> DatabaseCatalog:
        db = self._database
        # Explicit lowercase aliases: MySQL returns information_schema column
        # labels in uppercase, so we pin the result keys ourselves.
        table_rows = await self._fetch_all(
            "SELECT table_name AS tbl FROM information_schema.tables "
            "WHERE table_schema=%s AND table_type='BASE TABLE' "
            "ORDER BY table_name",
            (db,),
        )
        column_rows = await self._fetch_all(
            "SELECT table_name AS tbl, column_name AS col, data_type AS dtype, "
            "is_nullable AS nullable, column_key AS ckey "
            "FROM information_schema.columns WHERE table_schema=%s "
            "ORDER BY table_name, ordinal_position",
            (db,),
        )
        fk_rows = await self._fetch_all(
            "SELECT table_name AS tbl, column_name AS col, "
            "referenced_table_name AS ref_tbl, referenced_column_name AS ref_col "
            "FROM information_schema.key_column_usage "
            "WHERE table_schema=%s AND referenced_table_name IS NOT NULL",
            (db,),
        )

        tables: dict[str, TableInfo] = {
            row["tbl"]: TableInfo(schema_name=db, name=row["tbl"]) for row in table_rows
        }
        for row in column_rows:
            table = tables.get(row["tbl"])
            if table is None:
                continue
            is_pk = row["ckey"] == "PRI"
            table.columns.append(
                ColumnInfo(
                    name=row["col"],
                    data_type=row["dtype"],
                    nullable=row["nullable"] == "YES",
                    is_primary_key=is_pk,
                )
            )
            if is_pk:
                table.primary_key.append(row["col"])
        for row in fk_rows:
            table = tables.get(row["tbl"])
            if table is None:
                continue
            table.foreign_keys.append(
                ForeignKey(
                    column=row["col"],
                    references_table=row["ref_tbl"],
                    references_column=row["ref_col"],
                )
            )
        return DatabaseCatalog(source_id=self._name, tables=list(tables.values()))

    async def profile(self, target: ProfileTarget) -> ColumnProfile:
        table = _quote_ident(target.table)
        column = _quote_ident(target.column)
        agg_rows = await self._fetch_all(
            f"SELECT COUNT(*) AS total, SUM({column} IS NULL) AS nulls, "
            f"COUNT(DISTINCT {column}) AS distinct_count, "
            f"MIN({column}) AS minv, MAX({column}) AS maxv FROM {table}"
        )
        agg = agg_rows[0] if agg_rows else {}
        total = int(agg.get("total") or 0)
        nulls = int(agg.get("nulls") or 0)
        sample_rows = await self._fetch_all(
            f"SELECT DISTINCT {column} AS v FROM {table} WHERE {column} IS NOT NULL LIMIT 5"
        )
        distinct = agg.get("distinct_count")
        return ColumnProfile(
            table=target.table,
            column=target.column,
            null_fraction=(nulls / total) if total else None,
            distinct_count=int(distinct) if distinct is not None else None,
            min_value=_to_jsonable(agg.get("minv")),
            max_value=_to_jsonable(agg.get("maxv")),
            sample_values=[_to_jsonable(row["v"]) for row in sample_rows],
        )

    async def execute(self, plan: ExecutionPlan) -> QueryResult:
        sql = plan.representation.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise DataConnectionError("ExecutionPlan.representation missing 'sql'")
        head = sql.lstrip().lower()
        if not (head.startswith("select") or head.startswith("with")):
            raise DataConnectionError("Only read-only SELECT/WITH queries are allowed")
        limit = int(plan.representation.get("limit", 1000))
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql)
            rows = await cur.fetchmany(limit + 1)
            columns = [ResultColumn(name=desc[0], data_type="") for desc in (cur.description or [])]
        truncated = len(rows) > limit
        rows = rows[:limit]
        jsonable = [{k: _to_jsonable(v) for k, v in row.items()} for row in rows]
        return QueryResult(
            columns=columns,
            rows=jsonable,
            row_count=len(jsonable),
            truncated=truncated,
        )

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
