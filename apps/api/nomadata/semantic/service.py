"""SemanticModel service — implements the interface over the storage repository.

Depends on ``storage`` (not a driver directly), so this package stays
driver-free per the architecture boundary.
"""

from __future__ import annotations

from nomadata.core.errors import NomaDataError
from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    MetricDefinition,
    PublishResult,
    SemanticGraph,
    SemanticModelVersion,
)
from nomadata.storage.semantic_repo import SemanticRepository


class SemanticModelNotFoundError(NomaDataError):
    def __init__(self, source_id: str) -> None:
        super().__init__(f"No published semantic model for source: {source_id!r}")
        self.source_id = source_id


class MetricNotFoundError(NomaDataError):
    def __init__(self, source_id: str, name: str) -> None:
        super().__init__(f"Metric {name!r} not found in source: {source_id!r}")
        self.source_id = source_id
        self.name = name


class SemanticModelService(SemanticModel):
    def __init__(self, repo: SemanticRepository) -> None:
        self._repo = repo

    async def load(self, source_id: str) -> SemanticGraph:
        graph = await self._repo.get_published(source_id)
        if graph is None:
            raise SemanticModelNotFoundError(source_id)
        return graph

    async def get_draft(self, source_id: str) -> SemanticGraph | None:
        return await self._repo.get_latest(source_id)

    async def save_draft(self, graph: SemanticGraph) -> SemanticGraph:
        return await self._repo.save_draft(graph)

    async def publish(self, graph: SemanticGraph) -> PublishResult:
        return await self._repo.publish(graph)

    async def list_versions(self, source_id: str) -> list[SemanticModelVersion]:
        return await self._repo.list_versions(source_id)

    async def resolve_metric(self, source_id: str, name: str) -> MetricDefinition:
        graph = await self.load(source_id)
        for metric in graph.metrics:
            if metric.name == name:
                return metric
        raise MetricNotFoundError(source_id, name)
