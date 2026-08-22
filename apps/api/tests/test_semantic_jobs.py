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


def test_the_scope_is_the_tables_the_naming_pass_kept_a_count_on() -> None:
    """Structure cannot tell a table anyone measures from one nobody does: the
    old rule called 95 of 122 tables facts, permission tables included. The
    naming pass answers it table by table and records the answer by leaving the
    row count in place or removing it, so the count is the scope."""
    from nomadata.core.models import Aggregation, MetricKind
    from nomadata.semantic.jobs import _measurable_entities

    measured = _entity_with(
        "a.transactions", "transactions", [("paid_at", "time"), ("amount", "number")]
    )
    # Structurally identical, but the naming pass took its count away.
    unmeasured = _entity_with(
        "a.role_user", "role_user", [("created_at", "time"), ("weight", "number")]
    )

    graph = SemanticGraph(
        source_id="s",
        entities=[measured, unmeasured],
        metrics=[
            MetricDefinition(
                name="Transactions Count",
                kind=MetricKind.base,
                entity_key="a.transactions",
                aggregation=Aggregation.count,
            )
        ],
    )

    assert {e.key for e in _measurable_entities(graph)} == {"a.transactions"}


def test_a_table_that_already_has_real_metrics_needs_no_proposals() -> None:
    from nomadata.core.models import Aggregation, MetricKind
    from nomadata.semantic.jobs import _measurable_entities

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

    assert _measurable_entities(graph) == []


# ----------------------------------------------------------------------
# Metric names are what formulas quote
# ----------------------------------------------------------------------


def test_a_duplicate_metric_name_is_broken_by_its_table() -> None:
    """Two metrics with one name is not untidiness, it is an ambiguity the
    compiler resolves by accident: it maps name→entity, the duplicate keeps
    whichever came last, and a sound ratio then resolves half of itself to the
    wrong table and is dropped as spanning two. One duplicate cost two working
    ratios on the source this was found on."""
    from nomadata.core.models import Aggregation, MetricKind
    from nomadata.semantic.jobs import _unique_metric_names

    fees = _entity_with("a.fees", "fees", [("amount", "number")])
    notices = _entity_with("a.notices", "notices", [("amount", "number")])
    notices = notices.model_copy(update={"name": "Thông báo"})
    graph = SemanticGraph(
        source_id="s",
        entities=[fees, notices],
        metrics=[
            MetricDefinition(
                name="Tổng phí sau thuế",
                kind=MetricKind.base,
                entity_key="a.fees",
                aggregation=Aggregation.sum,
                column="amount",
            ),
            MetricDefinition(
                name="Tổng phí sau thuế",
                kind=MetricKind.base,
                entity_key="a.notices",
                aggregation=Aggregation.sum,
                column="amount",
            ),
        ],
    )

    names = [m.name for m in _unique_metric_names(graph).metrics]

    # The first keeps the plain name: renaming both would leave every reader
    # wondering what the other one is. Broken by the table, not by the entity
    # name — an entity name can be a duplicate itself.
    assert names == ["Tổng phí sau thuế", "Tổng phí sau thuế — notices"]


def test_names_that_are_already_distinct_are_left_alone() -> None:
    from nomadata.core.models import Aggregation, MetricKind
    from nomadata.semantic.jobs import _unique_metric_names

    graph = SemanticGraph(
        source_id="s",
        entities=[_entity_with("a.fees", "fees", [("amount", "number")])],
        metrics=[
            MetricDefinition(
                name="Tổng phí",
                kind=MetricKind.base,
                entity_key="a.fees",
                aggregation=Aggregation.sum,
                column="amount",
            ),
            MetricDefinition(
                name="Số phí",
                kind=MetricKind.base,
                entity_key="a.fees",
                aggregation=Aggregation.count,
            ),
        ],
    )

    assert [m.name for m in _unique_metric_names(graph).metrics] == [
        "Tổng phí",
        "Số phí",
    ]


def test_two_entities_the_ai_gave_one_name_are_told_apart() -> None:
    """The heuristic names an entity after its table, so it cannot collide. The
    AI pass runs in parallel batches, so the batch naming `contracts` cannot
    know what the batch naming `enterprise_contracts` chose — and both are
    reasonably called "Hợp đồng doanh nghiệp". No prompt fixes that; seeing the
    whole model at once is what batching gives up."""
    from nomadata.core.models import Aggregation, MetricKind
    from nomadata.semantic.jobs import _unique_entity_names

    a = _entity_with("s.contracts", "contracts", [("x", "number")])
    b = _entity_with("s.enterprise_contracts", "enterprise_contracts", [("x", "number")])
    named = "Hợp đồng doanh nghiệp"
    graph = SemanticGraph(
        source_id="s",
        entities=[
            a.model_copy(update={"name": named}),
            b.model_copy(update={"name": named}),
        ],
        metrics=[
            MetricDefinition(
                name=f"{named} Count",
                kind=MetricKind.base,
                entity_key="s.contracts",
                aggregation=Aggregation.count,
            ),
            MetricDefinition(
                name=f"{named} Count",
                kind=MetricKind.base,
                entity_key="s.enterprise_contracts",
                aggregation=Aggregation.count,
            ),
        ],
    )

    out = _unique_entity_names(graph)

    assert [e.name for e in out.entities] == [
        named,
        f"{named} — enterprise_contracts",
    ]
    # The default count label follows its entity, or the list shows a metric
    # named after something no longer called that.
    assert [m.name for m in out.metrics] == [
        f"{named} Count",
        f"{named} — enterprise_contracts Count",
    ]


def test_entities_already_distinct_are_left_alone() -> None:
    from nomadata.semantic.jobs import _unique_entity_names

    graph = SemanticGraph(
        source_id="s",
        entities=[
            _entity_with("s.a", "a", [("x", "number")]),
            _entity_with("s.b", "b", [("x", "number")]),
        ],
    )

    assert _unique_entity_names(graph) is graph
