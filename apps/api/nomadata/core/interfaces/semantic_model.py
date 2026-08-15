"""SemanticModel — load/publish/resolve the persistent semantic artifact.

The semantic model is a first-class, persistent artifact — not a prompt string.
The AI proposes; a human publishes; this artifact is the contract between AI and
database.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nomadata.core.models import (
    MetricDefinition,
    PublishResult,
    SemanticGraph,
    SemanticModelVersion,
)


class SemanticModel(ABC):
    @abstractmethod
    async def load(self, source_id: str) -> SemanticGraph:
        """Load the published semantic graph for a data source."""

    @abstractmethod
    async def get_draft(self, source_id: str) -> SemanticGraph | None:
        """Load the latest graph (any status) for editing, or None if none exists."""

    @abstractmethod
    async def save_draft(self, graph: SemanticGraph) -> SemanticGraph:
        """Persist a new draft version and return it (with its assigned version)."""

    @abstractmethod
    async def publish(self, graph: SemanticGraph) -> PublishResult:
        """Persist a human-reviewed semantic graph as the new published version."""

    @abstractmethod
    async def list_versions(self, source_id: str) -> list[SemanticModelVersion]:
        """List stored versions for a source, newest first."""

    @abstractmethod
    async def resolve_metric(self, source_id: str, name: str) -> MetricDefinition:
        """Resolve a metric in a source's published model to its definition."""
