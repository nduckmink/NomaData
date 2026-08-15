"""DataSource implementations.

The ONLY package allowed to import a database driver (aiomysql, ...).
"""

from __future__ import annotations

from nomadata.core.errors import ConfigurationError
from nomadata.core.interfaces.data_source import DataSource


def build_data_source(
    kind: str,
    *,
    name: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
) -> DataSource:
    """Construct a DataSource for the given engine kind.

    Drivers are imported lazily so the driver is only loaded when a source of
    that kind is actually configured.
    """
    normalized = kind.strip().lower()
    if normalized == "mysql":
        from nomadata.connectors.mysql import MySQLDataSource

        return MySQLDataSource(
            name=name,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
    if normalized in ("sqlserver", "mssql"):
        from nomadata.connectors.sqlserver import SQLServerDataSource

        return SQLServerDataSource(
            name=name,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
    raise ConfigurationError(f"Unsupported data source kind: {kind!r}")
