"""AnalyticalQuery -> Cube load query translation."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from nomadata.core.models import (
    FILTER_OPERATORS,
    RELATIVE_RANGES,
    VALUELESS_OPERATORS,
    AnalyticalQuery,
    Filter,
    TimeGrain,
    TimeSpec,
)
from nomadata.query.cube import (
    DEFAULT_ROWS,
    MAX_ROWS,
    QueryEngineError,
    build_cube_query,
)


def test_translates_all_parts() -> None:
    q = AnalyticalQuery(
        measures=["orders.revenue"],
        dimensions=["orders.status"],
        filters=[Filter(field="orders.status", operator="eq", value="SUCCESS")],
        time=TimeSpec(dimension="orders.created_at", range="this_year", grain=TimeGrain.month),
        limit=10,
        order_by=["-orders.revenue"],
    )
    cube = build_cube_query(q)

    assert cube["measures"] == ["orders.revenue"]
    assert cube["dimensions"] == ["orders.status"]
    assert cube["filters"][0] == {
        "member": "orders.status",
        "operator": "equals",
        "values": ["SUCCESS"],
    }
    td = cube["timeDimensions"][0]
    assert td["dimension"] == "orders.created_at"
    assert td["granularity"] == "month"
    assert td["dateRange"] == "this year"  # underscores → spaces for Cube
    assert cube["limit"] == 10
    assert cube["order"] == [["orders.revenue", "desc"]]


def test_an_empty_query_still_carries_a_row_ceiling() -> None:
    """Sending no limit means Cube's own default (10,000 rows) — too many to
    return over the API, and far too many to put in a model's context."""
    assert build_cube_query(AnalyticalQuery()) == {"limit": DEFAULT_ROWS}


def test_a_caller_limit_is_honoured_below_the_ceiling() -> None:
    assert build_cube_query(AnalyticalQuery(limit=10))["limit"] == 10


def test_a_caller_limit_cannot_exceed_the_ceiling() -> None:
    assert build_cube_query(AnalyticalQuery(limit=50_000))["limit"] == MAX_ROWS


# ----------------------------------------------------------------------
# Operator translation — the adapter must know every operator, or say so
# ----------------------------------------------------------------------


@pytest.mark.parametrize("operator", sorted(FILTER_OPERATORS))
def test_every_known_operator_can_be_translated(operator: str) -> None:
    """Walks the whole operator set, so adding one without teaching this
    adapter fails here instead of silently becoming `equals` in production."""
    value = None if operator in VALUELESS_OPERATORS else "X"
    query = AnalyticalQuery(filters=[Filter(field="orders.status", operator=operator, value=value)])

    [compiled] = build_cube_query(query)["filters"]

    assert compiled["member"] == "orders.status"
    assert compiled["operator"]


def test_negations_are_not_flattened_into_equality() -> None:
    """`not_in` falling back to `equals` inverted the filter and returned a
    perfectly normal-looking wrong number."""
    query = AnalyticalQuery(
        filters=[Filter(field="orders.status", operator="not_in", value=["A", "B"])]
    )

    [compiled] = build_cube_query(query)["filters"]

    assert compiled["operator"] == "notEquals"
    assert compiled["values"] == ["A", "B"]


def test_presence_operators_send_no_values() -> None:
    query = AnalyticalQuery(filters=[Filter(field="orders.paid_at", operator="not_set")])

    [compiled] = build_cube_query(query)["filters"]

    assert compiled == {"member": "orders.paid_at", "operator": "notSet"}


def test_an_operator_the_engine_cannot_run_raises() -> None:
    """Constructed past `Filter`'s own validation — e.g. loaded from an older
    row. The engine still refuses rather than guessing."""
    rogue = Filter(field="orders.status", operator="eq", value="X")
    object.__setattr__(rogue, "operator", "between")
    query = AnalyticalQuery()
    object.__setattr__(query, "filters", [rogue])

    with pytest.raises(QueryEngineError, match="between"):
        build_cube_query(query)


# ----------------------------------------------------------------------
# Time ranges — a controlled vocabulary, not free text
# ----------------------------------------------------------------------


@pytest.mark.parametrize("keyword", sorted(RELATIVE_RANGES))
def test_every_relative_range_reaches_cube(keyword: str) -> None:
    query = AnalyticalQuery(
        measures=["orders.revenue"],
        time=TimeSpec(dimension="orders.paid_at", range=keyword),
    )

    [td] = build_cube_query(query)["timeDimensions"]

    assert td["dateRange"] == keyword.replace("_", " ")


def test_an_invented_range_is_refused_at_the_edge() -> None:
    """A model writing "thang_nay" or "last_3_months" must fail here with a
    readable message, not deep inside Cube where it means nothing to the asker."""
    with pytest.raises(ValidationError, match="Unknown time range"):
        TimeSpec(dimension="orders.paid_at", range="last_3_months")


def test_range_spelling_is_normalised() -> None:
    """ "This Month" and "this-month" mean the same thing as "this_month"."""
    assert TimeSpec(dimension="d", range="This Month").range == "this_month"
    assert TimeSpec(dimension="d", range="last-week").range == "last_week"


def test_an_exact_window_travels_as_two_dates() -> None:
    query = AnalyticalQuery(
        measures=["orders.revenue"],
        time=TimeSpec(
            dimension="orders.paid_at",
            since=date(2026, 1, 1),
            until=date(2026, 6, 30),
        ),
    )

    [td] = build_cube_query(query)["timeDimensions"]

    assert td["dateRange"] == ["2026-01-01", "2026-06-30"]


def test_half_a_window_is_refused() -> None:
    with pytest.raises(ValidationError, match="both"):
        TimeSpec(dimension="d", since=date(2026, 1, 1))


def test_a_backwards_window_is_refused() -> None:
    with pytest.raises(ValidationError, match="after"):
        TimeSpec(dimension="d", since=date(2026, 6, 30), until=date(2026, 1, 1))


def test_a_time_axis_without_a_period_covers_everything() -> None:
    """Asking for a metric by month across all history is a real question."""
    query = AnalyticalQuery(
        measures=["orders.revenue"],
        time=TimeSpec(dimension="orders.paid_at", grain=TimeGrain.month),
    )

    [td] = build_cube_query(query)["timeDimensions"]

    assert td["granularity"] == "month"
    assert "dateRange" not in td


# ----------------------------------------------------------------------
# Timezone — what "this month" actually means
# ----------------------------------------------------------------------


def test_a_relative_period_carries_its_timezone() -> None:
    """Cube reads relative periods in UTC unless told otherwise. In UTC+7 that
    puts the first seven hours of every day in the day before — and at a month
    boundary, in the previous month."""
    query = AnalyticalQuery(
        measures=["orders.revenue"],
        time=TimeSpec(dimension="orders.paid_at", range="this_month", timezone="Asia/Ho_Chi_Minh"),
    )

    assert build_cube_query(query)["timezone"] == "Asia/Ho_Chi_Minh"


def test_an_unknown_timezone_is_refused_not_ignored() -> None:
    """Falling back to UTC on a typo is the silent-wrong-number path again."""
    with pytest.raises(ValidationError, match="Unknown timezone"):
        TimeSpec(dimension="d", range="this_month", timezone="Asia/Saigonn")


def test_no_timezone_means_no_key_rather_than_a_guess() -> None:
    query = AnalyticalQuery(measures=["orders.revenue"], time=TimeSpec(dimension="orders.paid_at"))
    assert "timezone" not in build_cube_query(query)


def test_an_exact_window_carries_the_zone_too() -> None:
    """Two ISO dates still need a zone: "2026-08-01 to 2026-08-31" starts at
    midnight somewhere."""
    query = AnalyticalQuery(
        measures=["orders.revenue"],
        time=TimeSpec(
            dimension="orders.paid_at",
            since=date(2026, 8, 1),
            until=date(2026, 8, 31),
            timezone="Asia/Ho_Chi_Minh",
        ),
    )

    compiled = build_cube_query(query)
    assert compiled["timezone"] == "Asia/Ho_Chi_Minh"
    assert compiled["timeDimensions"][0]["dateRange"] == ["2026-08-01", "2026-08-31"]


# ----------------------------------------------------------------------
# Reading the result back
# ----------------------------------------------------------------------


def _columns(rows: list[dict[str, object]], annotation: dict[str, object]) -> list[str]:
    from nomadata.query.cube import CubeQueryEngine

    return [
        c.name
        for c in CubeQueryEngine._columns(rows, {"annotation": annotation})  # noqa: SLF001
    ]


def test_the_column_grouped_by_is_a_column() -> None:
    """A total by month came back as six numbers with no months beside them:
    Cube annotates timeDimensions separately and only measures and dimensions
    were read, so the column the result was about was missing from it."""
    rows = [
        {
            "t.transaction_date.month": "2026-05-01",
            "t.Tong": 1_100_485_425,
            "t.transaction_date": "2026-05-01",
        }
    ]
    annotation = {
        "measures": {"t.Tong": {"type": "number"}},
        "dimensions": {},
        "timeDimensions": {
            "t.transaction_date": {"type": "time"},
            "t.transaction_date.month": {"type": "time"},
        },
    }

    # The granular one only: the bare date beside it is the same day again.
    assert _columns(rows, annotation) == ["t.transaction_date.month", "t.Tong"]


def test_what_the_rows_are_about_comes_before_what_was_measured() -> None:
    rows = [{"t.status": "paid", "t.Tong": 10, "t.transaction_date.month": "2026-05-01"}]
    annotation = {
        "measures": {"t.Tong": {"type": "number"}},
        "dimensions": {"t.status": {"type": "string"}},
        "timeDimensions": {"t.transaction_date.month": {"type": "time"}},
    }

    assert _columns(rows, annotation) == [
        "t.transaction_date.month",
        "t.status",
        "t.Tong",
    ]


def test_a_member_cube_named_but_did_not_return_is_not_a_column() -> None:
    rows = [{"t.Tong": 10}]
    annotation = {
        "measures": {"t.Tong": {"type": "number"}},
        "dimensions": {"t.status": {"type": "string"}},
        "timeDimensions": {},
    }

    assert _columns(rows, annotation) == ["t.Tong"]


def test_without_an_annotation_the_rows_still_describe_themselves() -> None:
    assert _columns([{"a": 1, "b": 2}], {}) == ["a", "b"]
    assert _columns([], {}) == []
