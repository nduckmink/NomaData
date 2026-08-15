"""Data source endpoints — manage connections, introspect schema, profile.

Connections are persisted in the app DB and (un)registered live via the
DataSourceManager, so create/update/delete take effect without a restart.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from nomadata.core.errors import DataSourceNotFoundError
from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.models import (
    ColumnProfile,
    ConnectionStatus,
    DatabaseCatalog,
    DataSourceConfig,
    DataSourceInfo,
    ProfileTarget,
)
from nomadata.core.registry import get_registry
from nomadata.datasource_manager import DataSourceManager
from nomadata.storage.data_source_repo import DataSourceExistsError

router = APIRouter(prefix="/datasources", tags=["data sources"])


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


@router.get("")
async def list_data_sources() -> list[str]:
    return get_registry().data_source_names()


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
    return config.model_copy(update={"name": name}).to_info()


@router.delete("/{name}", status_code=204)
async def delete_data_source(request: Request, name: str) -> Response:
    deleted = await _manager(request).delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Data source not found: {name!r}")
    return Response(status_code=204)


@router.post("/{name}/test", response_model=ConnectionStatus)
async def test_data_source(name: str) -> ConnectionStatus:
    return await _get_source(name).test_connection()


@router.get("/{name}/schema", response_model=DatabaseCatalog)
async def get_schema(name: str) -> DatabaseCatalog:
    return await _get_source(name).inspect_schema()


@router.get("/{name}/profile", response_model=ColumnProfile)
async def profile_column(name: str, table: str, column: str) -> ColumnProfile:
    return await _get_source(name).profile(ProfileTarget(table=table, column=column))
