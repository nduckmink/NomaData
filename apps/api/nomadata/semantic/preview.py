"""Run a single metric against the real database — the "Chạy thử" button.

A business user cannot read a Cube model, but they know their own numbers. Being
able to press one button and see *1.284.500.000 ₫ (12.847 rows)* is what turns a
definition from plausible into verified — and it catches the single most common
modelling mistake, measuring over the wrong date column.

Two deliberate choices:

- **Not through Cube.** Cube only knows the *published* model, and the whole
  point is to check a draft before publishing it. The SQL here is built
  mechanically from the structured definition — no LLM is involved, so this is
  not text-to-SQL; it is the same compilation Cube would do, done locally.
- **Read-only and bounded.** One aggregate row, identifiers quoted per dialect,
  values passed as literals only after being coerced by type. The connector
  independently refuses anything that isn't a SELECT.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.models import (
    VALUELESS_OPERATORS,
    Aggregation,
    DimensionKind,
    Entity,
    ExecutionPlan,
    Filter,
    MetricDefinition,
    MetricKind,
    MetricPreview,
    SemanticGraph,
)

_SQL_AGGREGATION = {
    Aggregation.count: "COUNT",
    Aggregation.count_distinct: "COUNT",
    Aggregation.sum: "SUM",
    Aggregation.avg: "AVG",
    Aggregation.min: "MIN",
    Aggregation.max: "MAX",
}

_SQL_OPERATOR = {
    "eq": "=",
    "neq": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


class PreviewError(Exception):
    """The metric cannot be turned into a runnable query."""


def _quote(identifier: str, kind: str) -> str:
    """Quote an identifier for the dialect. The identifier is always a column or
    table name we matched against the catalog first, but quoting keeps a table
    called ``order`` (or anything with a space) working."""
    if kind == "sqlserver":
        return "[" + identifier.replace("]", "]]") + "]"
    return "`" + identifier.replace("`", "``") + "`"


def _literal(value: Any, dimension_kind: DimensionKind | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)) and dimension_kind != DimensionKind.string:
        return str(value)
    if isinstance(value, (datetime, date)):
        return "'" + value.isoformat() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def _condition(f: Filter, entity: Entity, kind: str) -> str:
    column = _quote(f.field, kind)
    dim = next((d for d in entity.dimensions if d.column == f.field), None)
    dim_kind = dim.kind if dim else None

    if f.operator in VALUELESS_OPERATORS:
        return f"{column} IS {'NOT NULL' if f.operator == 'set' else 'NULL'}"
    if f.operator in ("in", "not_in"):
        values = f.value if isinstance(f.value, list) else [f.value]
        rendered = ", ".join(_literal(v, dim_kind) for v in values) or "NULL"
        return f"{column} {'NOT IN' if f.operator == 'not_in' else 'IN'} ({rendered})"
    if f.operator == "contains":
        escaped = str(f.value).replace("'", "''").replace("%", r"\%")
        return f"{column} LIKE '%{escaped}%'"
    operator = _SQL_OPERATOR.get(f.operator)
    if operator is None:
        # Unreachable via the API (Filter validates its operator), but a wrong
        # number is worse than an error, so never fall back to equality.
        raise PreviewError(f"Filter operator {f.operator!r} cannot be previewed.")
    return f"{column} {operator} {_literal(f.value, dim_kind)}"


def build_preview_sql(metric: MetricDefinition, entity: Entity, *, kind: str = "mysql") -> str:
    """Compile one base metric into a single-row aggregate query."""
    if metric.kind == MetricKind.derived:
        raise PreviewError("Derived metrics are computed from other metrics — preview the parts.")
    if metric.aggregation is None:
        raise PreviewError("This metric has no aggregation yet.")

    function = _SQL_AGGREGATION[metric.aggregation]
    if metric.aggregation == Aggregation.count:
        expression = "COUNT(*)"
    elif not metric.column:
        raise PreviewError(f"{metric.aggregation} needs a column.")
    elif metric.aggregation == Aggregation.count_distinct:
        expression = f"COUNT(DISTINCT {_quote(metric.column, kind)})"
    else:
        expression = f"{function}({_quote(metric.column, kind)})"

    table = _quote(entity.table, kind)
    if entity.schema_name and kind == "sqlserver":
        table = f"{_quote(entity.schema_name, kind)}.{table}"

    columns = [f"{expression} AS value", "COUNT(*) AS matched_rows"]
    if metric.time_dimension:
        # Showing the span the number covers is what catches a metric measured
        # by the wrong date column — the figure alone looks fine either way.
        time_column = _quote(metric.time_dimension, kind)
        columns.append(f"MIN({time_column}) AS period_start")
        columns.append(f"MAX({time_column}) AS period_end")

    sql = f"SELECT {', '.join(columns)} FROM {table}"
    conditions = [_condition(f, entity, kind) for f in metric.filters]
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    return sql


async def preview_metric(
    metric: MetricDefinition,
    graph: SemanticGraph,
    source: DataSource,
    *,
    kind: str = "mysql",
) -> MetricPreview:
    """Compile and run the metric, returning the number or a readable error.

    Failures are returned inside the result rather than raised: a preview that
    cannot run is information for the user, not an API error.
    """
    entity = next((e for e in graph.entities if e.key == metric.entity_key), None)
    if entity is None:
        return MetricPreview(
            metric_id=metric.id,
            error="This metric is not attached to an entity.",
        )
    try:
        sql = build_preview_sql(metric, entity, kind=kind)
    except PreviewError as exc:
        return MetricPreview(metric_id=metric.id, error=str(exc))

    try:
        result = await source.execute(
            ExecutionPlan(source_id=graph.source_id, representation={"sql": sql, "limit": 1})
        )
    except Exception as exc:  # noqa: BLE001 - shown to the user, not a server fault
        return MetricPreview(metric_id=metric.id, sql=sql, error=str(exc))

    row = result.rows[0] if result.rows else {}
    return MetricPreview(
        metric_id=metric.id,
        value=row.get("value"),
        row_count=int(row["matched_rows"]) if row.get("matched_rows") is not None else None,
        period_start=row.get("period_start"),
        period_end=row.get("period_end"),
        time_column=metric.time_dimension,
        sql=sql,
    )
