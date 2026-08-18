"""The publish gate — a model that cannot run must not report itself live."""

from __future__ import annotations

from nomadata.core.models import (
    Aggregation,
    ColumnInfo,
    DatabaseCatalog,
    Dimension,
    DimensionKind,
    Entity,
    Filter,
    MetricDefinition,
    MetricKind,
    SemanticGraph,
    TableInfo,
)
from nomadata.semantic.validator import validate_graph

ORDERS = "public.orders"


def _catalog() -> DatabaseCatalog:
    return DatabaseCatalog(
        source_id="shop",
        tables=[
            TableInfo(
                name="orders",
                columns=[
                    ColumnInfo(name="id", data_type="int", is_primary_key=True),
                    ColumnInfo(name="amount", data_type="decimal"),
                    ColumnInfo(name="status", data_type="varchar"),
                    ColumnInfo(name="paid_at", data_type="datetime"),
                ],
                primary_key=["id"],
            )
        ],
    )


def _entity() -> Entity:
    return Entity(
        key=ORDERS,
        name="Đơn hàng",
        table="orders",
        primary_key="id",
        description="Orders placed by customers.",
        dimensions=[
            Dimension(
                name="Trạng thái",
                column="status",
                kind=DimensionKind.string,
                sample_values=["NEW", "PAID"],
            ),
            Dimension(name="Ngày thu", column="paid_at", kind=DimensionKind.time),
            Dimension(name="Số tiền", column="amount", kind=DimensionKind.number),
        ],
    )


def _graph(*metrics: MetricDefinition) -> SemanticGraph:
    return SemanticGraph(source_id="shop", entities=[_entity()], metrics=list(metrics))


def _revenue(**overrides: object) -> MetricDefinition:
    base = {
        "name": "Doanh thu",
        "description": "Paid order value.",
        "kind": MetricKind.base,
        "entity_key": ORDERS,
        "aggregation": Aggregation.sum,
        "column": "amount",
    }
    return MetricDefinition(**{**base, **overrides})  # type: ignore[arg-type]


def _codes(graph: SemanticGraph) -> set[str]:
    return {i.code for i in validate_graph(graph, _catalog()).issues}


def test_a_sound_model_passes() -> None:
    report = validate_graph(_graph(_revenue()), _catalog())
    assert report.ok
    assert not report.errors


def test_metric_without_an_entity_is_an_error() -> None:
    """This is the exact shape the old rename bug produced: the metric compiles
    to nothing, so publishing it would ship a model with no measures."""
    report = validate_graph(_graph(_revenue(entity_key="public.gone")), _catalog())
    assert not report.ok
    assert "metric_entity_missing" in {i.code for i in report.errors}


def test_sum_needs_a_column() -> None:
    assert "metric_no_column" in _codes(_graph(_revenue(column=None)))


def test_sum_needs_a_numeric_column() -> None:
    assert "metric_column_not_numeric" in _codes(_graph(_revenue(column="status")))


def test_column_must_still_exist_in_the_database() -> None:
    assert "metric_column_missing" in _codes(_graph(_revenue(column="ghost")))


def test_time_dimension_must_be_temporal() -> None:
    assert "metric_time_not_temporal" in _codes(_graph(_revenue(time_dimension="status")))


def test_filter_column_must_exist() -> None:
    metric = _revenue(filters=[Filter(field="ghost", operator="eq", value="X")])
    assert "filter_column_missing" in _codes(_graph(metric))


def test_unseen_filter_value_warns_but_does_not_block() -> None:
    metric = _revenue(filters=[Filter(field="status", operator="eq", value="PAIDD")])
    report = validate_graph(_graph(metric), _catalog())
    assert report.ok  # a typo is a warning: sampled values are not exhaustive
    assert "filter_value_unseen" in {i.code for i in report.warnings}


def test_duplicate_metric_names_are_an_error() -> None:
    assert "duplicate_metric_name" in _codes(_graph(_revenue(), _revenue()))


def test_derived_expression_must_reference_real_metrics() -> None:
    derived = MetricDefinition(name="AOV", kind=MetricKind.derived, expression="Doanh thu / Số đơn")
    assert "derived_unknown_metric" in _codes(_graph(_revenue(), derived))


def test_a_model_with_no_metrics_cannot_be_published() -> None:
    assert "no_metrics" in _codes(_graph())


def test_validation_still_works_without_a_live_catalog() -> None:
    """A database that can't be reached must not stop the structural checks."""
    report = validate_graph(_graph(_revenue(entity_key="public.gone")), None)
    assert not report.ok
