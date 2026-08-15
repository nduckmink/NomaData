"""QueryEngine — turns an AnalyticalQuery into results.

The LLM produces an ``AnalyticalQuery`` (measures / dimensions / filters / time),
never SQL. Implementations (e.g. a Cube adapter under ``nomadata.query``)
translate that intermediate representation into the underlying engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nomadata.core.models import AnalyticalQuery, ExecutionPlan, QueryResult


class QueryEngine(ABC):
    @abstractmethod
    async def plan(self, query: AnalyticalQuery) -> ExecutionPlan:
        """Compile an analytical query into an engine-specific execution plan."""

    @abstractmethod
    async def run(self, query: AnalyticalQuery) -> QueryResult:
        """Plan and execute an analytical query, returning verified results."""
