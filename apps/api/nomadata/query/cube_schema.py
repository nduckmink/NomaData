"""Generate a Cube data model from a published SemanticGraph.

NomaData authors the semantic model; Cube executes queries. This module compiles
one into the other: each entity becomes a cube (over its table), dimensions and
base metrics become Cube dimensions/measures, and relationships become joins.
The output is a Cube YAML model written under ``cube/model/`` — Cube (dev mode)
hot-reloads it.

Whatever is not compiled here does not exist at query time, so this module is
deliberately strict: it refuses to emit a filter it cannot express, and it
carries the reviewed business names through as ``title`` — the names are the
entire point of the semantic layer, and dropping them wasted the review.

Derived metrics compile to Cube's calculated measures: ``type: number`` with a
``sql`` that references other measures by name. Cube can only do that within one
cube, so a formula whose parts live on different entities is left out — and the
validator says so rather than letting it disappear.

Cube-specific concepts stay here; the rest of the app speaks SemanticGraph.
Multi-source is out of scope for the single-source MVP.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

from nomadata.core.models import (
    VALUELESS_OPERATORS,
    Aggregation,
    DimensionKind,
    Filter,
    MetricKind,
    SemanticGraph,
)

# Cube measure "type" for each aggregation.
_CUBE_AGG: dict[Aggregation, str] = {
    Aggregation.count: "count",
    Aggregation.count_distinct: "count_distinct",
    Aggregation.sum: "sum",
    Aggregation.avg: "avg",
    Aggregation.min: "min",
    Aggregation.max: "max",
}

_CUBE_DIM_TYPE: dict[DimensionKind, str] = {
    DimensionKind.time: "time",
    DimensionKind.number: "number",
    DimensionKind.boolean: "boolean",
    DimensionKind.string: "string",
}

_OP_SQL = {
    "eq": "=",
    "neq": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


class CubeCompileError(Exception):
    """The graph contains something Cube cannot be asked to run."""


def _ident(text: str) -> str:
    """A safe Cube identifier: ASCII letters/digits/underscore, starting with a
    letter. Vietnamese names are folded (``Tỷ lệ hủy`` → ``Ty_le_huy``) because
    Cube identifiers must be ASCII — Python's ``\\w`` would have let the accents
    through and Cube would reject the model."""
    folded = unicodedata.normalize("NFKD", text.strip())
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"\W+", "_", ascii_only).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"c_{cleaned}" if cleaned else "c"
    return cleaned


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _filter_sql(f: Filter) -> dict[str, str]:
    """Compile one metric filter to a Cube measure filter.

    An operator with no SQL mapping raises rather than silently degrading to
    equality: a wrong number that looks right is the worst outcome available.
    """
    column = f"{{CUBE}}.{f.field}"
    if f.operator in VALUELESS_OPERATORS:
        return {"sql": f"{column} IS {'NOT NULL' if f.operator == 'set' else 'NULL'}"}
    if f.operator in ("in", "not_in"):
        values = f.value if isinstance(f.value, list) else [f.value]
        rendered = ", ".join(_literal(v) for v in values)
        keyword = "NOT IN" if f.operator == "not_in" else "IN"
        return {"sql": f"{column} {keyword} ({rendered})"}
    if f.operator == "contains":
        escaped = str(f.value).replace("'", "''")
        return {"sql": f"{column} LIKE '%{escaped}%'"}
    operator = _OP_SQL.get(f.operator)
    if operator is None:
        raise CubeCompileError(
            f"Filter operator {f.operator!r} on {f.field!r} cannot be compiled to Cube."
        )
    return {"sql": f"{column} {operator} {_literal(f.value)}"}


def _sql_table(table: str, schema_name: str) -> str:
    """Schema-qualify the table. Without this, SQL Server models resolved
    against the connection's default schema instead of ``dbo`` (or whichever
    schema the table was actually introspected from)."""
    if schema_name and schema_name != "public":
        return f"{schema_name}.{table}"
    return table


def _referenced_metrics(expression: str, names: list[str]) -> list[str]:
    """Which metric names a formula mentions.

    Longest first, so ``Revenue`` does not match inside ``Net Revenue`` and
    leave a dangling fragment behind.
    """
    found: list[str] = []
    remaining = expression
    for candidate in sorted(names, key=len, reverse=True):
        if candidate and candidate in remaining:
            found.append(candidate)
            remaining = remaining.replace(candidate, " ")
    return found


def _derived_sql(expression: str, measure_names: dict[str, str]) -> str:
    """Rewrite a business formula into Cube's ``{measure}`` references.

    ``Revenue / Order Count`` becomes ``{Revenue} / {Order_Count}`` — the names
    the user wrote, mapped to the identifiers the measures were compiled under.
    """
    rewritten = expression
    for name in sorted(measure_names, key=len, reverse=True):
        rewritten = rewritten.replace(name, "{" + measure_names[name] + "}")
    return rewritten


def build_cube_model(graph: SemanticGraph) -> dict:
    """Return the Cube model as a plain dict (``{"cubes": [...]}``)."""
    entities = [e for e in graph.entities if not e.hidden]
    by_key = {e.key: e for e in entities}

    metrics_by_entity: dict[str, list] = {}
    for m in graph.metrics:
        if m.kind != MetricKind.base or m.aggregation is None:
            continue
        if m.entity_key in by_key:
            metrics_by_entity.setdefault(m.entity_key, []).append(m)

    # A derived metric belongs to the cube its parts belong to. One that spans
    # cubes cannot be expressed as a calculated measure, so it is skipped here
    # and reported by the validator.
    base_names = {
        m.name: m.entity_key
        for m in graph.metrics
        if m.kind == MetricKind.base and m.entity_key in by_key and m.name.strip()
    }
    derived_by_entity: dict[str, list] = {}
    for m in graph.metrics:
        if m.kind != MetricKind.derived or not (m.expression or "").strip():
            continue
        parts = _referenced_metrics(m.expression or "", list(base_names))
        owners = {base_names[p] for p in parts}
        if len(owners) == 1:
            derived_by_entity.setdefault(owners.pop() or "", []).append(m)

    # Cube identifiers must be unique across the model.
    cube_names: dict[str, str] = {}
    used_cubes: set[str] = set()
    for entity in entities:
        name = _ident(entity.table)
        base, index = name, 2
        while name in used_cubes:
            name, index = f"{base}_{index}", index + 1
        used_cubes.add(name)
        cube_names[entity.key] = name

    cubes = []
    for entity in entities:
        # `data_source` tells Cube's driverFactory which connection (from the app
        # DB, configured in the UI) to run this cube against.
        cube: dict = {
            "name": cube_names[entity.key],
            "sql_table": _sql_table(entity.table, entity.schema_name),
            "data_source": graph.source_id,
        }
        # The reviewed business name reaches the query layer here — without it,
        # the agent would still be looking at raw table names.
        if entity.name and entity.name != entity.table:
            cube["title"] = entity.name
        if entity.description:
            cube["description"] = entity.description

        dimensions = [
            {
                "name": _ident(entity.primary_key),
                "sql": entity.primary_key,
                "type": "number",
                "primary_key": True,
            }
        ]
        used_dims = {dimensions[0]["name"]}
        # A column some metric measures time by has to be published even if it
        # was hidden as a slicing dimension: the metric points at it.
        time_columns = {
            m.time_dimension
            for m in metrics_by_entity.get(entity.key, [])
            if m.time_dimension
        }
        for d in entity.dimensions:
            if d.hidden and d.column not in time_columns:
                continue
            name = _ident(d.column)
            if name in used_dims:
                continue
            used_dims.add(name)
            dimension: dict = {
                "name": name,
                "sql": d.column,
                "type": _CUBE_DIM_TYPE[d.kind],
            }
            if d.name and d.name != d.column:
                dimension["title"] = d.name
            if d.description:
                dimension["description"] = d.description
            dimensions.append(dimension)
        cube["dimensions"] = dimensions

        measures = []
        used_measures: set[str] = set()
        for m in metrics_by_entity.get(entity.key, []):
            name = _ident(m.name)
            base, index = name, 2
            while name in used_measures:
                name, index = f"{base}_{index}", index + 1
            used_measures.add(name)
            measure: dict = {"name": name, "type": _CUBE_AGG[m.aggregation]}
            if m.name and m.name != name:
                measure["title"] = m.name
            if m.aggregation != Aggregation.count and m.column:
                measure["sql"] = m.column
            if m.description:
                measure["description"] = m.description
            if m.filters:
                measure["filters"] = [_filter_sql(f) for f in m.filters]
            if m.format:
                measure["format"] = "currency" if m.format == "currency" else m.format
            if m.time_dimension:
                # Which date this metric is measured over. Cube keeps time on the
                # cube, not the measure, so this travels as metadata and comes
                # back in the query annotation — that is how a caller knows to
                # ask by `paid_at` rather than `created_at`.
                measure["meta"] = {"default_time_dimension": _ident(m.time_dimension)}
            measures.append(measure)

        # Calculated measures come last: they reference the identifiers the
        # base measures above were just given.
        by_metric_name = {
            m.name: measure["name"]
            for m, measure in zip(metrics_by_entity.get(entity.key, []), measures, strict=True)
        }
        for m in derived_by_entity.get(entity.key, []):
            name = _ident(m.name)
            base, index = name, 2
            while name in used_measures:
                name, index = f"{base}_{index}", index + 1
            used_measures.add(name)
            derived: dict = {
                "name": name,
                "type": "number",
                "sql": _derived_sql(m.expression or "", by_metric_name),
            }
            if m.name and m.name != name:
                derived["title"] = m.name
            if m.description:
                derived["description"] = m.description
            if m.format:
                derived["format"] = m.format
            measures.append(derived)

        cube["measures"] = measures

        joins = [
            {
                "name": cube_names[r.to_entity_key],
                "relationship": r.kind,
                "sql": (
                    f"{{CUBE}}.{r.from_column} = {{{cube_names[r.to_entity_key]}}}.{r.to_column}"
                ),
            }
            for r in graph.relationships
            if r.from_entity_key == entity.key and r.to_entity_key in cube_names
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


def remove_cube_model(source_id: str, model_dir: str) -> bool:
    """Delete a source's generated Cube file.

    Called when a semantic model or its data source is deleted — an orphaned
    file stays queryable in Cube and would answer with data nobody expects.
    """
    path = Path(model_dir) / f"{_ident(source_id)}.yml"
    if path.exists():
        path.unlink()
        return True
    return False
