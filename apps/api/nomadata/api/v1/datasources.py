"""Data source endpoints — manage connections, introspect schema, profile.

Connections are persisted in the app DB and (un)registered live via the
DataSourceManager, so create/update/delete take effect without a restart.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query, Request, Response

from nomadata.connectors import build_data_source
from nomadata.core.errors import ConfigurationError, DataSourceNotFoundError
from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.models import (
    ColumnProfile,
    ConnectionStatus,
    DatabaseCatalog,
    DataSourceConfig,
    DataSourceInfo,
    ProfileTarget,
    TableInfo,
    TablePage,
    TableSummary,
)
from nomadata.core.registry import get_registry
from nomadata.datasource_manager import DataSourceManager
from nomadata.storage.data_source_repo import DataSourceExistsError

router = APIRouter(prefix="/datasources", tags=["data sources"])

# Introspection re-walks every table/column on each call — expensive, and the
# result barely changes between requests. Cache it per source so scrolling
# the table list or opening the diagram after browsing the list doesn't
# re-hit the database each time. Invalidated on our own edit/delete; a short
# TTL covers schema drift from outside NomaData.
_CATALOG_TTL_S = 300.0
_catalog_cache: dict[str, tuple[float, DatabaseCatalog]] = {}


async def _cached_catalog(name: str) -> DatabaseCatalog:
    cached = _catalog_cache.get(name)
    if cached is not None and time.monotonic() - cached[0] < _CATALOG_TTL_S:
        return cached[1]
    catalog = await _get_source(name).inspect_schema()
    _catalog_cache[name] = (time.monotonic(), catalog)
    return catalog


def _invalidate_catalog(name: str) -> None:
    _catalog_cache.pop(name, None)


def _get_source(name: str) -> DataSource:
    try:
        return get_registry().get_data_source(name)
    except DataSourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Data source not found: {name!r}") from None


def _manager(request: Request) -> DataSourceManager:
    manager = getattr(request.app.state, "datasource_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Data source management unavailable — app database not connected.",
        )
    return manager


@router.get("", response_model=list[DataSourceInfo])
async def list_data_sources(request: Request) -> list[DataSourceInfo]:
    """Full connection info (not just names) — the schema page renders a
    per-source engine logo, which needs ``kind``."""
    manager = getattr(request.app.state, "datasource_manager", None)
    if manager is None:
        return []
    infos = [await manager.get_info(name) for name in get_registry().data_source_names()]
    return [info for info in infos if info is not None]


@router.post("/verify", response_model=ConnectionStatus)
async def verify_data_source(request: Request, config: DataSourceConfig) -> ConnectionStatus:
    """Test a connection config WITHOUT saving it (used by the add/edit form)."""
    manager = getattr(request.app.state, "datasource_manager", None)
    if manager is not None:
        # Edit form may leave the password blank — reuse the stored secret.
        config = await manager.resolve_config(config)
    try:
        source = build_data_source(
            config.kind,
            name=config.name or "verify",
            host=config.host,
            port=config.port,
            database=config.database,
            user=config.user,
            password=config.resolve_password(),
        )
    except ConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    try:
        return await source.test_connection()
    finally:
        close = getattr(source, "close", None)
        if close is not None:
            await close()


@router.get("/{name}", response_model=DataSourceInfo)
async def get_data_source(request: Request, name: str) -> DataSourceInfo:
    info = await _manager(request).get_info(name)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Data source not found: {name!r}")
    return info


@router.post("", response_model=DataSourceInfo, status_code=201)
async def create_data_source(request: Request, config: DataSourceConfig) -> DataSourceInfo:
    try:
        created = await _manager(request).create(config)
    except DataSourceExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return created.to_info()


@router.put("/{name}", response_model=DataSourceInfo)
async def update_data_source(
    request: Request, name: str, config: DataSourceConfig
) -> DataSourceInfo:
    updated = await _manager(request).update(name, config)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Data source not found: {name!r}")
    _invalidate_catalog(name)
    return config.model_copy(update={"name": name}).to_info()


@router.delete("/{name}", status_code=204)
async def delete_data_source(request: Request, name: str) -> Response:
    deleted = await _manager(request).delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Data source not found: {name!r}")
    _invalidate_catalog(name)
    return Response(status_code=204)


@router.post("/{name}/test", response_model=ConnectionStatus)
async def test_data_source(name: str) -> ConnectionStatus:
    return await _get_source(name).test_connection()


@router.get("/{name}/schema", response_model=DatabaseCatalog)
async def get_schema(name: str) -> DatabaseCatalog:
    """Full catalog — only the diagram needs every table at once."""
    return await _cached_catalog(name)


@router.get("/{name}/tables", response_model=TablePage)
async def list_tables(
    name: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=40, ge=1, le=200),
    q: str = "",
) -> TablePage:
    """Paginated, search-filtered table list — no columns, so scrolling
    doesn't pull the whole catalog over the wire one page at a time."""
    catalog = await _cached_catalog(name)
    matches = catalog.tables
    if q:
        needle = q.strip().lower()
        matches = [t for t in matches if needle in t.name.lower()]
    page = matches[offset : offset + limit]
    return TablePage(
        items=[
            TableSummary(
                schema_name=t.schema_name,
                name=t.name,
                column_count=len(t.columns),
                foreign_key_count=len(t.foreign_keys),
            )
            for t in page
        ],
        total=len(matches),
        total_tables=len(catalog.tables),
        total_columns=sum(len(t.columns) for t in catalog.tables),
        total_relationships=sum(len(t.foreign_keys) for t in catalog.tables),
    )


@router.get("/{name}/tables/{table}", response_model=TableInfo)
async def get_table(name: str, table: str) -> TableInfo:
    """One table's full columns/keys — fetched on selection, not upfront."""
    catalog = await _cached_catalog(name)
    for t in catalog.tables:
        if t.name == table:
            return t
    raise HTTPException(status_code=404, detail=f"Table not found: {table!r}")


@router.get("/{name}/profile", response_model=ColumnProfile)
async def profile_column(name: str, table: str, column: str) -> ColumnProfile:
    return await _get_source(name).profile(ProfileTarget(table=table, column=column))
