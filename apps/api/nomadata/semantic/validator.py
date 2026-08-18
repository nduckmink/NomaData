"""Validate a SemanticGraph before it is allowed to become a published model.

Publishing used to succeed whatever the graph contained, and a Cube compile
failure was swallowed into a log line — so a model could report "published"
while producing no measures at all. This module is that missing gate.

Errors block a publish. Warnings are shown but do not: a metric with no
description is untidy, not broken.

The check is pure (graph in, report out) so the same call powers both the
Publish button and the live badge in the editor.
"""

from __future__ import annotations

import re

from nomadata.core.models import (
    Aggregation,
    DatabaseCatalog,
    DimensionKind,
    Entity,
    IssueLevel,
    MetricDefinition,
    MetricKind,
    SemanticGraph,
    ValidationIssue,
    ValidationReport,
)

#: Aggregations that need a numeric column. ``count`` needs none;
#: ``count_distinct``/``min``/``max`` work on any type.
_NUMERIC_ONLY = {Aggregation.sum, Aggregation.avg}
_NEEDS_COLUMN = {
    Aggregation.count_distinct,
    Aggregation.sum,
    Aggregation.avg,
    Aggregation.min,
    Aggregation.max,
}

#: Metric names referenced inside a derived expression.
_NAME_TOKEN = re.compile(r"[A-Za-zÀ-ỹ_][A-Za-zÀ-ỹ0-9_ ]*")


def _issue(
    level: IssueLevel, code: str, message: str, target: str | None, kind: str
) -> ValidationIssue:
    return ValidationIssue(level=level, code=code, message=message, target=target, target_kind=kind)


def validate_graph(
    graph: SemanticGraph, catalog: DatabaseCatalog | None = None
) -> ValidationReport:
    """Check a graph for anything that would break at query time.

    ``catalog`` is optional: without it the structural checks still run, they
    just can't confirm that a column still exists in the database.
    """
    issues: list[ValidationIssue] = []
    entities = {e.key: e for e in graph.entities}
    columns_by_table: dict[str, dict[str, str]] = {}
    if catalog is not None:
        columns_by_table = {
            t.name: {c.name: c.data_type for c in t.columns} for t in catalog.tables
        }

    _check_entities(graph, columns_by_table, issues)
    _check_metrics(graph, entities, columns_by_table, issues)
    _check_relationships(graph, entities, issues)

    report = ValidationReport(issues=issues)
    report.ok = not report.errors
    return report


def _check_entities(
    graph: SemanticGraph,
    columns_by_table: dict[str, dict[str, str]],
    issues: list[ValidationIssue],
) -> None:
    seen_keys: set[str] = set()
    seen_names: dict[str, str] = {}
    for entity in graph.entities:
        if entity.key in seen_keys:
            issues.append(
                _issue(
                    IssueLevel.error,
                    "duplicate_entity_key",
                    f"Two entities share the key {entity.key!r}.",
                    entity.key,
                    "entity",
                )
            )
        seen_keys.add(entity.key)

        if not entity.name.strip():
            issues.append(
                _issue(
                    IssueLevel.error,
                    "entity_unnamed",
                    f"Table {entity.table!r} has no business name.",
                    entity.key,
                    "entity",
                )
            )
        elif entity.name in seen_names and seen_names[entity.name] != entity.key:
            issues.append(
                _issue(
                    IssueLevel.warning,
                    "duplicate_entity_name",
                    f"Another entity is also called {entity.name!r} — pickers will be ambiguous.",
                    entity.key,
                    "entity",
                )
            )
        else:
            seen_names[entity.name] = entity.key

        if not entity.description or not entity.description.strip():
            issues.append(
                _issue(
                    IssueLevel.warning,
                    "entity_undescribed",
                    f"{entity.name!r} has no description — the AI has less to reason with.",
                    entity.key,
                    "entity",
                )
            )

        table_columns = columns_by_table.get(entity.table)
        if columns_by_table and table_columns is None:
            issues.append(
                _issue(
                    IssueLevel.error,
                    "table_missing",
                    f"Table {entity.table!r} no longer exists in the database.",
                    entity.key,
                    "entity",
                )
            )
            continue
        if table_columns is not None and entity.primary_key not in table_columns:
            issues.append(
                _issue(
                    IssueLevel.error,
                    "primary_key_missing",
                    f"Primary key column {entity.primary_key!r} is not in {entity.table!r}.",
                    entity.key,
                    "entity",
                )
            )
        if table_columns is not None:
            for dim in entity.dimensions:
                if dim.column not in table_columns:
                    issues.append(
                        _issue(
                            IssueLevel.error,
                            "dimension_column_missing",
                            f"Column {dim.column!r} is not in {entity.table!r}.",
                            entity.key,
                            "entity",
                        )
                    )


def _check_metrics(
    graph: SemanticGraph,
    entities: dict[str, Entity],
    columns_by_table: dict[str, dict[str, str]],
    issues: list[ValidationIssue],
) -> None:
    metric_names = {m.name.strip() for m in graph.metrics if m.name.strip()}
    seen_names: set[str] = set()

    if not graph.metrics:
        issues.append(
            _issue(
                IssueLevel.error,
                "no_metrics",
                "The model has no metrics — there would be nothing to measure.",
                None,
                "metric",
            )
        )

    for metric in graph.metrics:
        name = metric.name.strip()
        if not name:
            issues.append(
                _issue(
                    IssueLevel.error,
                    "metric_unnamed",
                    "A metric has no name.",
                    metric.id,
                    "metric",
                )
            )
        elif name in seen_names:
            issues.append(
                _issue(
                    IssueLevel.error,
                    "duplicate_metric_name",
                    f"Two metrics are both called {name!r}; derived expressions "
                    "could not tell them apart.",
                    metric.id,
                    "metric",
                )
            )
        else:
            seen_names.add(name)

        if metric.kind == MetricKind.derived:
            _check_derived(metric, graph, metric_names, issues)
            continue

        _check_base(metric, graph, entities, columns_by_table, issues)


def _check_base(
    metric: MetricDefinition,
    graph: SemanticGraph,
    entities: dict[str, Entity],
    columns_by_table: dict[str, dict[str, str]],
    issues: list[ValidationIssue],
) -> None:
    entity = entities.get(metric.entity_key or "")
    if entity is None:
        issues.append(
            _issue(
                IssueLevel.error,
                "metric_entity_missing",
                f"Metric {metric.name!r} does not belong to any entity "
                "— it would be dropped from the published model.",
                metric.id,
                "metric",
            )
        )
        return

    table = entity.table
    dimensions = {d.column: d for d in entity.dimensions}
    table_columns = columns_by_table.get(table)

    if metric.aggregation is None:
        issues.append(
            _issue(
                IssueLevel.error,
                "metric_no_aggregation",
                f"Metric {metric.name!r} has no aggregation.",
                metric.id,
                "metric",
            )
        )
        return

    if metric.aggregation in _NEEDS_COLUMN and not metric.column:
        issues.append(
            _issue(
                IssueLevel.error,
                "metric_no_column",
                f"{metric.aggregation} needs a column; {metric.name!r} has none.",
                metric.id,
                "metric",
            )
        )
    elif metric.column:
        if table_columns is not None and metric.column not in table_columns:
            issues.append(
                _issue(
                    IssueLevel.error,
                    "metric_column_missing",
                    f"Column {metric.column!r} is not in {table!r}.",
                    metric.id,
                    "metric",
                )
            )
        elif metric.aggregation in _NUMERIC_ONLY:
            dim = dimensions.get(metric.column)
            if dim is not None and dim.kind not in (DimensionKind.number,):
                issues.append(
                    _issue(
                        IssueLevel.error,
                        "metric_column_not_numeric",
                        f"{metric.aggregation} needs a numeric column, but "
                        f"{metric.column!r} is {dim.kind}.",
                        metric.id,
                        "metric",
                    )
                )

    if metric.time_dimension:
        dim = dimensions.get(metric.time_dimension)
        if dim is None and table_columns is not None:
            if metric.time_dimension not in table_columns:
                issues.append(
                    _issue(
                        IssueLevel.error,
                        "metric_time_missing",
                        f"Time column {metric.time_dimension!r} is not in {table!r}.",
                        metric.id,
                        "metric",
                    )
                )
        elif dim is not None and dim.kind != DimensionKind.time:
            issues.append(
                _issue(
                    IssueLevel.error,
                    "metric_time_not_temporal",
                    f"{metric.time_dimension!r} is not a date/time column.",
                    metric.id,
                    "metric",
                )
            )

    for f in metric.filters:
        if table_columns is not None and f.field not in table_columns:
            issues.append(
                _issue(
                    IssueLevel.error,
                    "filter_column_missing",
                    f"Filter column {f.field!r} is not in {table!r}.",
                    metric.id,
                    "metric",
                )
            )
            continue
        dim = dimensions.get(f.field)
        if dim is not None and dim.sample_values and f.operator == "eq":
            known = {str(v) for v in dim.sample_values}
            if known and str(f.value) not in known:
                issues.append(
                    _issue(
                        IssueLevel.warning,
                        "filter_value_unseen",
                        f"{f.value!r} was not among the sampled values of "
                        f"{f.field!r} — check the spelling.",
                        metric.id,
                        "metric",
                    )
                )


def _check_derived(
    metric: MetricDefinition,
    graph: SemanticGraph,
    metric_names: set[str],
    issues: list[ValidationIssue],
) -> None:
    expression = (metric.expression or "").strip()
    if not expression:
        issues.append(
            _issue(
                IssueLevel.error,
                "derived_no_expression",
                f"Derived metric {metric.name!r} has no expression.",
                metric.id,
                "metric",
            )
        )
        return
    unknown = [
        token.strip()
        for token in _NAME_TOKEN.findall(expression)
        if token.strip() and token.strip() not in metric_names
    ]
    for name in unknown:
        issues.append(
            _issue(
                IssueLevel.error,
                "derived_unknown_metric",
                f"{metric.name!r} refers to {name!r}, which is not a metric.",
                metric.id,
                "metric",
            )
        )
    if unknown:
        return

    # The query engine builds a calculated measure inside one cube, so a formula
    # whose parts sit on different entities cannot be executed. Saying so beats
    # letting it vanish from the published model.
    owners = {
        m.entity_key
        for m in graph.metrics
        if m.kind != MetricKind.derived and m.name.strip() and m.name in expression
    }
    if len(owners) > 1:
        issues.append(
            _issue(
                IssueLevel.warning,
                "derived_spans_entities",
                f"{metric.name!r} combines metrics from different entities, so it "
                "cannot be published as a calculated metric yet.",
                metric.id,
                "metric",
            )
        )


def _check_relationships(
    graph: SemanticGraph, entities: dict[str, Entity], issues: list[ValidationIssue]
) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    for rel in graph.relationships:
        missing = False
        for key, side in ((rel.from_entity_key, "from"), (rel.to_entity_key, "to")):
            if key not in entities:
                missing = True
                issues.append(
                    _issue(
                        IssueLevel.error,
                        "relationship_entity_missing",
                        f"A relationship points at {key!r} on its {side} side, "
                        "which is not an entity in this model.",
                        key,
                        "relationship",
                    )
                )
        if missing:
            continue

        # A join on a column that isn't there fails at query time, not here, so
        # it has to be caught before publish.
        for key, column, side in (
            (rel.from_entity_key, rel.from_column, "from"),
            (rel.to_entity_key, rel.to_column, "to"),
        ):
            entity = entities[key]
            known = {d.column for d in entity.dimensions} | {entity.primary_key}
            if column not in known:
                issues.append(
                    _issue(
                        IssueLevel.error,
                        "relationship_column_missing",
                        f"Join column {column!r} is not on {entity.name!r} "
                        f"({side} side of the link).",
                        key,
                        "relationship",
                    )
                )

        signature = (
            rel.from_entity_key,
            rel.from_column,
            rel.to_entity_key,
            rel.to_column,
        )
        if signature in seen:
            issues.append(
                _issue(
                    IssueLevel.warning,
                    "duplicate_relationship",
                    f"{entities[rel.from_entity_key].name!r} is linked to "
                    f"{entities[rel.to_entity_key].name!r} twice the same way.",
                    rel.from_entity_key,
                    "relationship",
                )
            )
        seen.add(signature)
