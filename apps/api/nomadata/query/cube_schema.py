"""Generate a Cube data model from a published SemanticGraph.

NomaData authors the semantic model; Cube executes queries. This module compiles
one into the other: each entity becomes a cube (over its table), dimensions and
base metrics become Cube dimensions/measures, and relationships become joins.
The output is a Cube YAML model written under ``cube/model/`` — Cube (dev mode)
hot-reloads it.

Cube-specific concepts stay here; the rest of the app speaks SemanticGraph.
Derived metrics and multi-source are out of scope for the single-source MVP.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from nomadata.core.models import Aggregation, Filter, MetricKind, SemanticGraph

# Cube measure "type" for each aggregation.
_CUBE_AGG: dict[Aggregation, str] = {
    Aggregation.count: "count",
    Aggregation.count_distinct: "count_distinct",
    Aggregation.sum: "sum",
    Aggregation.avg: "avg",
    Aggregation.min: "min",
    Aggregation.max: "max",
}

_OP_SQL = {
    "eq": "=",
    "neq": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def _ident(text: str) -> str:
    """A valid Cube identifier (letters/digits/underscore, starts with a letter)."""
    cleaned = re.sub(r"\W+", "_", text.strip()).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"c_{cleaned}" if cleaned else "c"
    return cleaned


def _dim_type(column: str) -> str:
    """Best-effort Cube dimension type from the column name (the graph doesn't
    carry column types). A real type check would need the catalog."""
    if re.search(r"(_at$|_date$|date|time)", column, re.IGNORECASE):
        return "time"
    if re.search(r"(_id$|amount|price|total|qty|quantity|count|number|score)", column, re.IGNORECASE):
        return "number"
    return "string"


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _filter_sql(f: Filter) -> dict[str, str]:
    op = _OP_SQL.get(f.operator, "=")
    return {"sql": f"{{CUBE}}.{f.field} {op} {_literal(f.value)}"}


def build_cube_model(graph: SemanticGraph) -> dict:
    """Return the Cube model as a plain dict (``{"cubes": [...]}``)."""
    table_of_entity = {e.name: e.table for e in graph.entities}
    entity_tables = {e.table for e in graph.entities}

    metrics_by_table: dict[str, list] = {}
    for m in graph.metrics:
        if m.kind != MetricKind.base or not m.entity or m.aggregation is None:
            continue  # derived metrics: MVP skips them
        table = table_of_entity.get(m.entity)
        if table is not None:
            metrics_by_table.setdefault(table, []).append(m)

    cubes = []
    for e in graph.entities:
        # `data_source` tells Cube's driverFactory which connection (from the app
        # DB, configured in the UI) to run this cube against.
        cube: dict = {
            "name": _ident(e.table),
            "sql_table": e.table,
            "data_source": graph.source_id,
        }
        if e.description:
            cube["description"] = e.description

        dimensions = [
            {
                "name": _ident(e.primary_key),
                "sql": e.primary_key,
                "type": _dim_type(e.primary_key),
                "primary_key": True,
            }
        ]
        for d in e.dimensions:
            dimensions.append(
                {"name": _ident(d.column), "sql": d.column, "type": _dim_type(d.column)}
            )
        cube["dimensions"] = dimensions

        measures = []
        used: set[str] = set()
        for m in metrics_by_table.get(e.table, []):
            name = _ident(m.name)
            base, i = name, 2
            while name in used:
                name, i = f"{base}_{i}", i + 1
            used.add(name)
            measure: dict = {"name": name, "type": _CUBE_AGG[m.aggregation]}
            if m.aggregation != Aggregation.count and m.column:
                measure["sql"] = m.column
            if m.description:
                measure["description"] = m.description
            if m.filters:
                measure["filters"] = [_filter_sql(f) for f in m.filters]
            measures.append(measure)
        cube["measures"] = measures

        joins = [
            {
                "name": _ident(r.to_entity),
                "relationship": r.kind,
                "sql": f"{{CUBE}}.{r.from_column} = {{{_ident(r.to_entity)}}}.{r.to_column}",
            }
            for r in graph.relationships
            if r.from_entity == e.table and r.to_entity in entity_tables
        ]
        if joins:
            cube["joins"] = joins

        cubes.append(cube)

    return {"cubes": cubes}


def generate_cube_yaml(graph: SemanticGraph) -> str:
    """Render the Cube model for a semantic graph as YAML."""
    return yaml.safe_dump(build_cube_model(graph), sort_keys=False, allow_unicode=True)


def write_cube_model(graph: SemanticGraph, model_dir: str) -> str:
    """Write one Cube YAML file per source into ``model_dir``. Returns the path."""
    directory = Path(model_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_ident(graph.source_id)}.yml"
    path.write_text(generate_cube_yaml(graph), encoding="utf-8")
    return str(path)
