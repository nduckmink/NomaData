"""SemanticJobRunner — dedup and active-job tracking (hermetic)."""

from __future__ import annotations

import asyncio

from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    JobStatus,
    MetricDefinition,
    PublishResult,
    SemanticGraph,
    SemanticModelVersion,
)
from nomadata.core.registry import Registry
from nomadata.semantic.jobs import SemanticJobRunner


class _NoopSemantic(SemanticModel):
    async def load(self, source_id: str) -> SemanticGraph:
        raise NotImplementedError

    async def get_draft(self, source_id: str) -> SemanticGraph | None:
        return None

    async def save_draft(self, graph: SemanticGraph) -> SemanticGraph:
        return graph

    async def publish(self, graph: SemanticGraph) -> PublishResult:
        raise NotImplementedError

    async def list_versions(self, source_id: str) -> list[SemanticModelVersion]:
        return []

    async def delete(self, source_id: str) -> int:
        return 0

    async def resolve_metric(self, source_id: str, name: str) -> MetricDefinition:
        raise NotImplementedError


async def test_generate_dedups_and_tracks_active() -> None:
    runner = SemanticJobRunner(Registry(), _NoopSemantic())

    job1 = runner.start_generate("shop", use_ai=False)
    job2 = runner.start_generate("shop", use_ai=False)
    # A second build for the same source returns the running one — no duplicate.
    assert job2.id == job1.id
    assert runner.active_for("shop") is job1

    # The task fails (no data source registered) → active is cleared afterwards.
    await asyncio.sleep(0.05)
    assert job1.status == JobStatus.error
    assert runner.active_for("shop") is None


async def test_active_for_unknown_source_is_none() -> None:
    runner = SemanticJobRunner(Registry(), _NoopSemantic())
    assert runner.active_for("nope") is None
