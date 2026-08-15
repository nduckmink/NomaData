"""SemanticModel — load/publish/resolve the persistent semantic artifact.

The semantic model is a first-class, persistent artifact — not a prompt string.
The AI proposes; a human publishes; this artifact is the contract between AI and
database.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nomadata.core.models import MetricDefinition, PublishResult, SemanticGraph


class SemanticModel(ABC):
    @abstractmethod
    async def load(self, source_id: str) -> SemanticGraph:
        """Load the published semantic graph for a data source."""

    @abstractmethod
    async def publish(self, graph: SemanticGraph) -> PublishResult:
        """Persist a human-reviewed semantic graph as the new published version."""

    @abstractmethod
    async def resolve_metric(self, name: str) -> MetricDefinition:
        """Resolve a metric to its authoritative business definition."""
