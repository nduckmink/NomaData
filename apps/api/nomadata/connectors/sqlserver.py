"""SQL Server data source connector.

Uses pymssql (FreeTDS) — a pip wheel with no system ODBC driver, so it installs
cleanly on Windows dev and in a slim Linux prod image alike. pymssql is
synchronous, so blocking calls run in a thread to fit the async DataSource
interface. A fresh connection is opened per operation (thread-safe; M1 usage is
low-frequency introspection).
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any

import pymssql

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
    """Bracket-quote a SQL Server identifier (prevents identifier injection)."""
    return "[" + name.replace("]", "]]") + "]"


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


class SQLServerDataSource(DataSource):
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

    @property
    def name(self) -> str:
        return self._name

    def _query_sync(self, sql: str) -> list[dict[str, Any]]:
        try:
            conn = pymssql.connect(
                server=self._host,
                port=str(self._port),
                user=self._user,
                password=self._password,
                database=self._database,
                login_timeout=10,
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as domain error
            raise DataConnectionError(
                f"SQL Server connection failed for {self._name!r}: {exc}"
            ) from exc
        try:
            cur = conn.cursor(as_dict=True)
            cur.execute(sql)
            return list(cur.fetchall())
        finally:
            conn.close()

    async def _fetch_all(self, sql: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._query_sync, sql)

    async def test_connection(self) -> ConnectionStatus:
        start = time.perf_counter()
        try:
            await self._fetch_all("SELECT 1 AS x")
        except Exception as exc:  # noqa: BLE001 - reported as status, not raised
            return ConnectionStatus(state=ConnectionState.error, message=str(exc))
        latency_ms = (time.perf_counter() - start) * 1000
        return ConnectionStatus(state=ConnectionState.ok, latency_ms=round(latency_ms, 2))

    async def inspect_schema(self) -> DatabaseCatalog:
        table_rows = await self._fetch_all(
            "SELECT TABLE_SCHEMA AS sch, TABLE_NAME AS tbl "
            "FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        column_rows = await self._fetch_all(
            "SELECT TABLE_SCHEMA AS sch, TABLE_NAME AS tbl, COLUMN_NAME AS col, "
            "DATA_TYPE AS dtype, IS_NULLABLE AS nullable "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
        )
        pk_rows = await self._fetch_all(
            "SELECT kcu.TABLE_SCHEMA AS sch, kcu.TABLE_NAME AS tbl, "
            "kcu.COLUMN_NAME AS col "
            "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
            "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
            "  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
            " AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA "
            "WHERE tc.CONSTRAINT_TYPE='PRIMARY KEY'"
        )
        fk_rows = await self._fetch_all(
            "SELECT s.name AS sch, t.name AS tbl, c.name AS col, "
            "rt.name AS ref_tbl, rc.name AS ref_col "
            "FROM sys.foreign_key_columns fkc "
            "JOIN sys.tables t ON fkc.parent_object_id = t.object_id "
            "JOIN sys.schemas s ON t.schema_id = s.schema_id "
            "JOIN sys.columns c ON fkc.parent_object_id = c.object_id "
            " AND fkc.parent_column_id = c.column_id "
            "JOIN sys.tables rt ON fkc.referenced_object_id = rt.object_id "
            "JOIN sys.columns rc ON fkc.referenced_object_id = rc.object_id "
            " AND fkc.referenced_column_id = rc.column_id"
        )

        def key(schema: str, table: str) -> str:
            return f"{schema}.{table}"

        pk_set = {(key(r["sch"], r["tbl"]), r["col"]) for r in pk_rows}
        tables: dict[str, TableInfo] = {
            key(r["sch"], r["tbl"]): TableInfo(schema_name=r["sch"], name=r["tbl"])
            for r in table_rows
        }
        for row in column_rows:
            table = tables.get(key(row["sch"], row["tbl"]))
            if table is None:
                continue
            is_pk = (key(row["sch"], row["tbl"]), row["col"]) in pk_set
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
            table = tables.get(key(row["sch"], row["tbl"]))
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
        schema = (
            target.schema_name if target.schema_name and target.schema_name != "public" else "dbo"
        )
        table = f"{_quote_ident(schema)}.{_quote_ident(target.table)}"
        column = _quote_ident(target.column)
        agg_rows = await self._fetch_all(
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) AS nulls, "
            f"COUNT(DISTINCT {column}) AS distinct_count, "
            f"MIN({column}) AS minv, MAX({column}) AS maxv FROM {table}"
        )
        agg = agg_rows[0] if agg_rows else {}
        total = int(agg.get("total") or 0)
        nulls = int(agg.get("nulls") or 0)
        sample_rows = await self._fetch_all(
            f"SELECT DISTINCT TOP 5 {column} AS v FROM {table} WHERE {column} IS NOT NULL"
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

        def run() -> tuple[list[dict[str, Any]], list[str]]:
            conn = pymssql.connect(
                server=self._host,
                port=str(self._port),
                user=self._user,
                password=self._password,
                database=self._database,
                login_timeout=10,
                timeout=60,
            )
            try:
                cur = conn.cursor(as_dict=True)
                cur.execute(sql)
                fetched = cur.fetchmany(limit + 1)
                names = [d[0] for d in (cur.description or [])]
                return list(fetched), names
            finally:
                conn.close()

        rows, names = await asyncio.to_thread(run)
        truncated = len(rows) > limit
        rows = rows[:limit]
        jsonable = [{k: _to_jsonable(v) for k, v in row.items()} for row in rows]
        return QueryResult(
            columns=[ResultColumn(name=n, data_type="") for n in names],
            rows=jsonable,
            row_count=len(jsonable),
            truncated=truncated,
        )
