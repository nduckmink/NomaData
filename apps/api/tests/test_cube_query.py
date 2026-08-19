"""AnalyticalQuery -> Cube load query translation."""

from __future__ import annotations

import pytest

from nomadata.core.models import (
    FILTER_OPERATORS,
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
    query = AnalyticalQuery(
        filters=[Filter(field="orders.status", operator=operator, value=value)]
    )

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
    query = AnalyticalQuery(
        filters=[Filter(field="orders.paid_at", operator="not_set")]
    )

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
