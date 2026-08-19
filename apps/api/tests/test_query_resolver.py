"""Business names in, runnable members out — or a sentence saying why not.

The load-bearing tests are the time-axis ones. A metric already records which
date it is measured over; letting a caller re-choose it per question is how a
total gets counted by `created_at` instead of `paid_at` — a figure that looks
completely normal and is wrong.
"""

from __future__ import annotations

import pytest

from nomadata.agent.resolver import (
    QueryValidationError,
    queryable_metrics,
    resolve,
)
from nomadata.core.models import (
    Aggregation,
    AnalyticalQuery,
    Dimension,
    DimensionKind,
    Entity,
    Filter,
    MetricDefinition,
    MetricKind,
    SemanticGraph,
    TimeSpec,
)

FEES = "app.hoc_phi"
STUDENTS = "app.hoc_sinh"


def _graph() -> SemanticGraph:
    return SemanticGraph(
        source_id="scp",
        entities=[
            Entity(
                key=FEES,
                name="Phiếu học phí",
                table="hoc_phi",
                primary_key="id",
                dimensions=[
                    Dimension(name="Trạng thái", column="trang_thai", kind=DimensionKind.string),
                    Dimension(name="Số tiền", column="so_tien", kind=DimensionKind.number),
                    Dimension(
                        name="Ngày thanh toán",
                        column="ngay_thanh_toan",
                        kind=DimensionKind.time,
                    ),
                    Dimension(name="Ngày tạo", column="ngay_tao", kind=DimensionKind.time),
                    Dimension(
                        name="Ghi chú", column="ghi_chu", kind=DimensionKind.string, hidden=True
                    ),
                ],
            ),
            Entity(
                key=STUDENTS,
                name="Học sinh",
                table="hoc_sinh",
                primary_key="id",
                dimensions=[Dimension(name="Cơ sở", column="co_so", kind=DimensionKind.string)],
            ),
        ],
        metrics=[
            MetricDefinition(
                name="Học phí đã thu",
                kind=MetricKind.base,
                entity_key=FEES,
                aggregation=Aggregation.sum,
                column="so_tien",
                time_dimension="ngay_thanh_toan",
            ),
            MetricDefinition(
                name="Số phiếu",
                kind=MetricKind.base,
                entity_key=FEES,
                aggregation=Aggregation.count,
                time_dimension="ngay_tao",
            ),
            MetricDefinition(
                name="Số học sinh",
                kind=MetricKind.base,
                entity_key=STUDENTS,
                aggregation=Aggregation.count,
            ),
        ],
    )


# ----------------------------------------------------------------------
# Translation
# ----------------------------------------------------------------------


def test_business_names_become_cube_members() -> None:
    resolved = resolve(
        AnalyticalQuery(measures=["Học phí đã thu"], dimensions=["Trạng thái"]), _graph()
    )

    assert resolved.measures == ["hoc_phi.Hoc_phi_da_thu"]
    assert resolved.dimensions == ["hoc_phi.trang_thai"]


def test_a_metric_id_works_as_well_as_its_name() -> None:
    graph = _graph()
    metric = graph.metrics[0]

    resolved = resolve(AnalyticalQuery(measures=[metric.id]), graph)

    assert resolved.measures == ["hoc_phi.Hoc_phi_da_thu"]


def test_filters_and_order_are_translated_too() -> None:
    resolved = resolve(
        AnalyticalQuery(
            measures=["Học phí đã thu"],
            filters=[Filter(field="Trạng thái", operator="eq", value="DA_THU")],
            order_by=["-Học phí đã thu"],
        ),
        _graph(),
    )

    assert resolved.filters[0].field == "hoc_phi.trang_thai"
    assert resolved.filters[0].value == "DA_THU"
    assert resolved.order_by == ["-hoc_phi.Hoc_phi_da_thu"]


# ----------------------------------------------------------------------
# The time axis — what the metric already decided
# ----------------------------------------------------------------------


def test_the_time_column_defaults_to_the_one_the_metric_declares() -> None:
    """The user asked for "this month" without naming a date column. The metric
    knows: a human chose `ngay_thanh_toan` when they published it."""
    resolved = resolve(
        AnalyticalQuery(
            measures=["Học phí đã thu"], time=TimeSpec(dimension="", range="this_month")
        ),
        _graph(),
    )

    assert resolved.time is not None
    assert resolved.time.dimension == "hoc_phi.ngay_thanh_toan"
    assert resolved.notes == []


def test_asking_by_another_date_is_allowed_but_reported() -> None:
    """Measuring receipts by their creation date is a legitimate question and a
    completely different number — so the answer has to say which one it is."""
    resolved = resolve(
        AnalyticalQuery(
            measures=["Học phí đã thu"], time=TimeSpec(dimension="Ngày tạo", range="this_month")
        ),
        _graph(),
    )

    assert resolved.time is not None
    assert resolved.time.dimension == "hoc_phi.ngay_tao"
    assert any("ngay_thanh_toan" in note for note in resolved.notes)


def test_metrics_on_different_time_axes_cannot_share_one() -> None:
    """ "Học phí đã thu" is measured by payment date and "Số phiếu" by creation
    date; one time filter over both would mix periods silently."""
    with pytest.raises(QueryValidationError) as caught:
        resolve(
            AnalyticalQuery(
                measures=["Học phí đã thu", "Số phiếu"],
                time=TimeSpec(dimension="", range="this_month"),
            ),
            _graph(),
        )

    assert "different dates" in str(caught.value)


def test_a_time_filter_with_nothing_to_measure_by_is_refused() -> None:
    with pytest.raises(QueryValidationError, match="does not declare one"):
        resolve(
            AnalyticalQuery(
                measures=["Số học sinh"], time=TimeSpec(dimension="", range="this_month")
            ),
            _graph(),
        )


def test_a_non_date_column_cannot_be_the_time_axis() -> None:
    with pytest.raises(QueryValidationError, match="not a date column"):
        resolve(
            AnalyticalQuery(
                measures=["Học phí đã thu"],
                time=TimeSpec(dimension="Trạng thái", range="this_month"),
            ),
            _graph(),
        )


def test_no_time_spec_means_no_time_axis() -> None:
    resolved = resolve(AnalyticalQuery(measures=["Học phí đã thu"]), _graph())
    assert resolved.time is None


# ----------------------------------------------------------------------
# Refusing usefully
# ----------------------------------------------------------------------


def test_an_unknown_metric_names_the_closest_real_one() -> None:
    with pytest.raises(QueryValidationError) as caught:
        resolve(AnalyticalQuery(measures=["Học phí da thu"]), _graph())

    error = caught.value
    assert error.field == "measures"
    assert error.did_you_mean == "Học phí đã thu"
    assert "Did you mean" in str(error)


def test_an_unknown_name_with_no_near_match_still_fails_clearly() -> None:
    with pytest.raises(QueryValidationError) as caught:
        resolve(AnalyticalQuery(measures=["zzzzzz"]), _graph())

    assert caught.value.did_you_mean == ""
    assert "No metric called" in str(caught.value)


def test_an_unknown_dimension_is_refused() -> None:
    with pytest.raises(QueryValidationError, match="No dimension called"):
        resolve(AnalyticalQuery(measures=["Số học sinh"], dimensions=["Quận"]), _graph())


def test_a_query_measuring_nothing_is_refused() -> None:
    with pytest.raises(QueryValidationError, match="measure something"):
        resolve(AnalyticalQuery(dimensions=["Trạng thái"]), _graph())


def test_hidden_dimensions_are_not_addressable() -> None:
    """What the published model hides, the query layer does not have."""
    with pytest.raises(QueryValidationError):
        resolve(AnalyticalQuery(measures=["Số phiếu"], dimensions=["Ghi chú"]), _graph())


def test_ordering_by_something_unknown_is_refused() -> None:
    with pytest.raises(QueryValidationError, match="Cannot order by"):
        resolve(AnalyticalQuery(measures=["Số phiếu"], order_by=["Doanh thu"]), _graph())


# ----------------------------------------------------------------------
# What may be offered at all
# ----------------------------------------------------------------------


def test_a_derived_metric_spanning_entities_is_not_queryable() -> None:
    """It cannot become a Cube calculated measure, so offering its name would
    only produce a query nothing can run."""
    graph = _graph()
    spanning = MetricDefinition(
        name="Học phí mỗi học sinh",
        kind=MetricKind.derived,
        expression="Học phí đã thu / Số học sinh",
    )
    graph = graph.model_copy(update={"metrics": [*graph.metrics, spanning]})

    offered = {m.name for m in queryable_metrics(graph)}

    assert "Học phí mỗi học sinh" not in offered
    assert "Học phí đã thu" in offered


def test_a_derived_metric_within_one_entity_is_queryable() -> None:
    graph = _graph()
    ratio = MetricDefinition(
        name="Trung bình mỗi phiếu",
        kind=MetricKind.derived,
        expression="Học phí đã thu / Số phiếu",
    )
    graph = graph.model_copy(update={"metrics": [*graph.metrics, ratio]})

    assert "Trung bình mỗi phiếu" in {m.name for m in queryable_metrics(graph)}
    assert resolve(AnalyticalQuery(measures=["Trung bình mỗi phiếu"]), graph).measures
