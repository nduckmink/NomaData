"""DataSource — provider-independent database access.

Implementations live under ``nomadata.connectors`` and are the ONLY modules
allowed to import a database driver. Everything else depends on this interface,
so adding a new database never touches the agent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nomadata.core.models import (
    ColumnProfile,
    ConnectionStatus,
    DatabaseCatalog,
    ExecutionPlan,
    ProfileTarget,
    QueryResult,
)


class DataSource(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier for this data source."""

    @abstractmethod
    async def test_connection(self) -> ConnectionStatus:
        """Verify connectivity."""

    @abstractmethod
    async def inspect_schema(self) -> DatabaseCatalog:
        """Introspect tables, columns, keys and relationships."""

    @abstractmethod
    async def profile(self, target: ProfileTarget) -> ColumnProfile:
        """Profile a single column (nulls, distinct, ranges, samples)."""

    @abstractmethod
    async def execute(self, plan: ExecutionPlan) -> QueryResult:
        """Execute an engine-produced plan and return rows."""
