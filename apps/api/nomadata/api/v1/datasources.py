"""Data source endpoints — connect, introspect schema, profile columns."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from nomadata.core.errors import DataSourceNotFoundError
from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.models import (
    ColumnProfile,
    ConnectionStatus,
    DatabaseCatalog,
    ProfileTarget,
)
from nomadata.core.registry import get_registry

router = APIRouter(prefix="/datasources", tags=["data sources"])


def _get_source(name: str) -> DataSource:
    try:
        return get_registry().get_data_source(name)
    except DataSourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Data source not found: {name!r}") from None


@router.get("")
async def list_data_sources() -> list[str]:
    return get_registry().data_source_names()


@router.post("/{name}/test", response_model=ConnectionStatus)
async def test_data_source(name: str) -> ConnectionStatus:
    return await _get_source(name).test_connection()


@router.get("/{name}/schema", response_model=DatabaseCatalog)
async def get_schema(name: str) -> DatabaseCatalog:
    return await _get_source(name).inspect_schema()


@router.get("/{name}/profile", response_model=ColumnProfile)
async def profile_column(name: str, table: str, column: str) -> ColumnProfile:
    return await _get_source(name).profile(ProfileTarget(table=table, column=column))
