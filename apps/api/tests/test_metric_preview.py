"""Running one metric against the real database — the "Chạy thử" button.

The SQL is compiled mechanically from the structured definition (no LLM), so
these tests pin the exact statement: a preview that silently measures something
else is worse than no preview at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.models import (
    Aggregation,
    ColumnProfile,
    ConnectionState,
    ConnectionStatus,
    DatabaseCatalog,
    Dimension,
    DimensionKind,
    Entity,
    ExecutionPlan,
    Filter,
    MetricDefinition,
    MetricKind,
    ProfileTarget,
    QueryResult,
    ResultColumn,
    SemanticGraph,
)
from nomadata.semantic.preview import PreviewError, build_preview_sql, preview_metric

ORDERS = "public.orders"


def _entity(schema_name: str = "public") -> Entity:
    return Entity(
        key=ORDERS,
        name="Phiếu học phí",
        table="hoc_phi",
        schema_name=schema_name,
        primary_key="id",
        dimensions=[
            Dimension(name="Trạng thái", column="trang_thai", kind=DimensionKind.string),
            Dimension(name="Số tiền", column="so_tien", kind=DimensionKind.number),
        ],
    )


def _metric(**overrides: Any) -> MetricDefinition:
    base: dict[str, Any] = {
        "name": "Học phí đã thu",
        "kind": MetricKind.base,
        "entity_key": ORDERS,
        "aggregation": Aggregation.sum,
        "column": "so_tien",
    }
    return MetricDefinition(**{**base, **overrides})


class _Source(DataSource):
    """Captures the SQL and returns a fixed aggregate row."""

    def __init__(self, value: Any = 1234.5, rows: int = 42) -> None:
        self.sql = ""
        self._value = value
        self._rows = rows

    @property
    def name(self) -> str:
        return "scp"

    async def test_connection(self) -> ConnectionStatus:
        return ConnectionStatus(state=ConnectionState.ok)

    async def inspect_schema(self) -> DatabaseCatalog:
        return DatabaseCatalog(source_id="scp")

    async def profile(self, target: ProfileTarget) -> ColumnProfile:
        return ColumnProfile(table=target.table, column=target.column)

    async def execute(self, plan: ExecutionPlan) -> QueryResult:
        self.sql = plan.representation["sql"]
        return QueryResult(
            columns=[ResultColumn(name="value", data_type="")],
            rows=[{"value": self._value, "matched_rows": self._rows}],
            row_count=1,
        )


def test_compiles_a_filtered_sum() -> None:
    metric = _metric(filters=[Filter(field="trang_thai", operator="eq", value="DA_THU")])
    sql = build_preview_sql(metric, _entity())
    assert sql == (
        "SELECT SUM(`so_tien`) AS value, COUNT(*) AS matched_rows "
        "FROM `hoc_phi` WHERE `trang_thai` = 'DA_THU'"
    )


def test_count_needs_no_column() -> None:
    sql = build_preview_sql(_metric(aggregation=Aggregation.count, column=None), _entity())
    assert sql.startswith("SELECT COUNT(*) AS value")


def test_count_distinct_is_not_a_plain_count() -> None:
    metric = _metric(aggregation=Aggregation.count_distinct, column="trang_thai")
    assert "COUNT(DISTINCT `trang_thai`)" in build_preview_sql(metric, _entity())


def test_in_filter_renders_a_value_list() -> None:
    metric = _metric(filters=[Filter(field="trang_thai", operator="in", value=["A", "B"])])
    assert "`trang_thai` IN ('A', 'B')" in build_preview_sql(metric, _entity())


def test_null_checks_take_no_value() -> None:
    metric = _metric(filters=[Filter(field="trang_thai", operator="set")])
    assert "`trang_thai` IS NOT NULL" in build_preview_sql(metric, _entity())


def test_string_values_are_escaped() -> None:
    metric = _metric(filters=[Filter(field="trang_thai", operator="eq", value="O'Brien")])
    assert "'O''Brien'" in build_preview_sql(metric, _entity())


def test_sqlserver_quotes_and_qualifies() -> None:
    sql = build_preview_sql(_metric(), _entity(schema_name="dbo"), kind="sqlserver")
    assert "FROM [dbo].[hoc_phi]" in sql
    assert "SUM([so_tien])" in sql


def test_derived_metrics_cannot_be_previewed_directly() -> None:
    metric = _metric(kind=MetricKind.derived, expression="A / B")
    with pytest.raises(PreviewError):
        build_preview_sql(metric, _entity())


async def test_preview_returns_the_number_and_the_sql() -> None:
    graph = SemanticGraph(source_id="scp", entities=[_entity()])
    source = _Source(value=1_284_500_000, rows=12_847)

    result = await preview_metric(_metric(), graph, source)

    assert result.value == 1_284_500_000
    assert result.row_count == 12_847
    assert "SUM(`so_tien`)" in result.sql  # traceable: the user can read the query
    assert result.error is None


async def test_a_failing_query_is_reported_not_raised() -> None:
    """A preview that cannot run is information for the user, not a 500."""

    class _Broken(_Source):
        async def execute(self, plan: ExecutionPlan) -> QueryResult:
            raise RuntimeError("Table 'hoc_phi' doesn't exist")

    graph = SemanticGraph(source_id="scp", entities=[_entity()])

    result = await preview_metric(_metric(), graph, _Broken())

    assert result.error is not None
    assert "hoc_phi" in result.error
    assert result.value is None


async def test_metric_without_an_entity_reports_clearly() -> None:
    graph = SemanticGraph(source_id="scp", entities=[])

    result = await preview_metric(_metric(), graph, _Source())

    assert result.error == "This metric is not attached to an entity."
