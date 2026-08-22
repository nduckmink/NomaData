"""QueryEngine — turns an AnalyticalQuery into results.

The LLM produces an ``AnalyticalQuery`` (measures / dimensions / filters / time),
never SQL. Implementations (e.g. a Cube adapter under ``nomadata.query``)
translate that intermediate representation into the underlying engine.

A query names things in business language, so every call carries the published
``SemanticGraph`` that gives those names meaning. Without it the caller would
have to know the engine's own identifiers — which is the engine leaking into
everything that talks to it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nomadata.core.models import (
    AnalyticalQuery,
    ExecutionPlan,
    QueryResult,
    SemanticGraph,
)


class QueryEngine(ABC):
    @abstractmethod
    async def plan(self, query: AnalyticalQuery, graph: SemanticGraph) -> ExecutionPlan:
        """Compile an analytical query into an engine-specific execution plan."""

    @abstractmethod
    async def run(self, query: AnalyticalQuery, graph: SemanticGraph) -> QueryResult:
        """Plan and execute an analytical query, returning verified results."""

    async def distinct_values(
        self, member: str, graph: SemanticGraph, *, limit: int = 25
    ) -> list[object]:
        """The values actually stored in one dimension.

        A model describes structure — a column called ``Status``, of type text —
        and says nothing about what is in it. Every filter is therefore a guess:
        the caller writes ``Status = "Đã hoàn thành"`` while the rows say
        ``COMPLETED``, and the answer comes back empty with no hint why.

        Asked at question time rather than profiled in advance, because the
        alternative scales with the size of the database instead of with the
        question, and goes stale the moment a new value appears.

        Engines that cannot enumerate values return nothing, and the caller does
        without rather than failing.
        """
        return []
