"""AnalyticalQuery -> Cube load query translation."""

from __future__ import annotations

from nomadata.core.models import AnalyticalQuery, Filter, TimeGrain, TimeSpec
from nomadata.query.cube import build_cube_query


def test_translates_all_parts() -> None:
    q = AnalyticalQuery(
        measures=["orders.revenue"],
        dimensions=["orders.status"],
        filters=[Filter(field="orders.status", operator="eq", value="SUCCESS")],
        time=TimeSpec(
            dimension="orders.created_at", range="this_year", grain=TimeGrain.month
        ),
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


def test_empty_query_is_empty() -> None:
    assert build_cube_query(AnalyticalQuery()) == {}
