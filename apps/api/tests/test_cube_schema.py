"""Cube model generation from a SemanticGraph."""

from __future__ import annotations

import yaml

from nomadata.core.models import (
    Aggregation,
    Dimension,
    Entity,
    Filter,
    MetricDefinition,
    MetricKind,
    Relationship,
    SemanticGraph,
)
from nomadata.query.cube_schema import generate_cube_yaml


def _graph() -> SemanticGraph:
    return SemanticGraph(
        source_id="shop",
        entities=[
            Entity(
                name="Orders",
                table="orders",
                primary_key="id",
                dimensions=[Dimension(name="Status", column="status")],
            ),
            Entity(
                name="Customers",
                table="customers",
                primary_key="id",
                dimensions=[Dimension(name="Signed Up", column="created_at")],
            ),
        ],
        metrics=[
            MetricDefinition(
                name="Orders Count",
                kind=MetricKind.base,
                entity="Orders",
                aggregation=Aggregation.count,
            ),
            MetricDefinition(
                name="Revenue",
                kind=MetricKind.base,
                entity="Orders",
                aggregation=Aggregation.sum,
                column="amount",
                filters=[Filter(field="status", operator="eq", value="SUCCESS")],
            ),
            # derived — MVP skips it
            MetricDefinition(
                name="AOV", kind=MetricKind.derived, expression="Revenue / Orders Count"
            ),
        ],
        relationships=[
            Relationship(
                from_entity="orders",
                to_entity="customers",
                from_column="customer_id",
                to_column="id",
                kind="many_to_one",
            )
        ],
    )


def test_generates_cube_per_entity_with_measures_and_joins() -> None:
    model = yaml.safe_load(generate_cube_yaml(_graph()))
    cubes = {c["name"]: c for c in model["cubes"]}
    assert set(cubes) == {"orders", "customers"}

    orders = cubes["orders"]
    assert orders["sql_table"] == "orders"
    assert orders["data_source"] == "shop"  # routes to the UI-configured connection

    measures = {m["name"]: m for m in orders["measures"]}
    assert measures["Orders_Count"]["type"] == "count"
    assert "sql" not in measures["Orders_Count"]  # count needs no column
    assert measures["Revenue"]["type"] == "sum"
    assert measures["Revenue"]["sql"] == "amount"
    # business filter compiled to a Cube measure filter
    assert measures["Revenue"]["filters"][0]["sql"] == "{CUBE}.status = 'SUCCESS'"

    # a derived metric produced no measure
    assert "AOV" not in measures

    # relationship → join to the referenced cube
    assert orders["joins"][0]["name"] == "customers"
    assert orders["joins"][0]["relationship"] == "many_to_one"
    assert "customer_id" in orders["joins"][0]["sql"]


def test_infers_dimension_types_and_primary_key() -> None:
    model = yaml.safe_load(generate_cube_yaml(_graph()))
    cubes = {c["name"]: c for c in model["cubes"]}

    orders_dims = {d["name"]: d for d in cubes["orders"]["dimensions"]}
    assert orders_dims["id"]["primary_key"] is True
    assert orders_dims["status"]["type"] == "string"

    customers_dims = {d["name"]: d for d in cubes["customers"]["dimensions"]}
    assert customers_dims["created_at"]["type"] == "time"  # name-based inference
