"""SemanticJobRunner — dedup and active-job tracking (hermetic)."""

from __future__ import annotations

import asyncio

from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import (
    Entity,
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


# ----------------------------------------------------------------------
# Fact-entity selection — which tables a build spends AI on for metrics
# ----------------------------------------------------------------------


def _entity_with(key: str, table: str, dims: list[tuple[str, str]]) -> Entity:
    from nomadata.core.models import Dimension, DimensionKind

    return Entity(
        key=key,
        name=table,
        table=table,
        primary_key="id",
        dimensions=[Dimension(name=c, column=c, kind=DimensionKind(k)) for c, k in dims],
    )


def test_fact_entities_needs_a_date_and_a_real_number() -> None:
    from nomadata.semantic.jobs import _fact_entities

    fact = _entity_with(
        "a.transactions",
        "transactions",
        [("paid_at", "time"), ("amount", "number"), ("status", "string")],
    )
    lookup = _entity_with("a.banks", "banks", [("name", "string")])
    # A junction table: a date but no measurable number (only foreign keys).
    junction = _entity_with(
        "a.role_user", "role_user", [("created_at", "time"), ("role_id", "number")]
    )

    graph = SemanticGraph(source_id="s", entities=[fact, lookup, junction])
    picked = {e.key for e in _fact_entities(graph)}

    assert picked == {"a.transactions"}


def test_fact_entities_skips_a_table_that_already_has_real_metrics() -> None:
    from nomadata.core.models import Aggregation, MetricKind
    from nomadata.semantic.jobs import _fact_entities

    fact = _entity_with(
        "a.transactions",
        "transactions",
        [("paid_at", "time"), ("amount", "number")],
    )
    graph = SemanticGraph(
        source_id="s",
        entities=[fact],
        metrics=[
            MetricDefinition(
                name="Revenue",
                kind=MetricKind.base,
                entity_key="a.transactions",
                aggregation=Aggregation.sum,
                column="amount",
            )
        ],
    )

    assert _fact_entities(graph) == []
