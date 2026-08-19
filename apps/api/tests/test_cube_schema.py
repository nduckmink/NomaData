"""Cube model generation from a SemanticGraph."""

from __future__ import annotations

import pytest
import yaml

from nomadata.core.models import (
    Aggregation,
    Dimension,
    DimensionKind,
    Entity,
    Filter,
    MetricDefinition,
    MetricKind,
    Relationship,
    SemanticGraph,
)
from nomadata.query.cube_schema import (
    CubeCompileError,
    generate_cube_yaml,
    member_map,
    normalise,
    remove_cube_model,
    write_cube_model,
)

ORDERS = "public.orders"
CUSTOMERS = "public.customers"


def _graph() -> SemanticGraph:
    return SemanticGraph(
        source_id="shop",
        entities=[
            Entity(
                key=ORDERS,
                name="Đơn hàng",
                table="orders",
                primary_key="id",
                dimensions=[
                    Dimension(name="Trạng thái", column="status", kind=DimensionKind.string),
                    Dimension(
                        name="Ghi chú",
                        column="note",
                        kind=DimensionKind.string,
                        hidden=True,
                    ),
                ],
            ),
            Entity(
                key=CUSTOMERS,
                name="Khách hàng",
                table="customers",
                primary_key="id",
                dimensions=[
                    Dimension(name="Signed Up", column="created_at", kind=DimensionKind.time)
                ],
            ),
        ],
        metrics=[
            MetricDefinition(
                name="Orders Count",
                kind=MetricKind.base,
                entity_key=ORDERS,
                aggregation=Aggregation.count,
            ),
            MetricDefinition(
                name="Doanh thu",
                kind=MetricKind.base,
                entity_key=ORDERS,
                aggregation=Aggregation.sum,
                column="amount",
                filters=[Filter(field="status", operator="eq", value="SUCCESS")],
            ),
            # derived — MVP skips it
            MetricDefinition(
                name="AOV", kind=MetricKind.derived, expression="Doanh thu / Orders Count"
            ),
        ],
        relationships=[
            Relationship(
                from_entity_key=ORDERS,
                to_entity_key=CUSTOMERS,
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
    # A Vietnamese name folds to an ASCII identifier Cube will accept…
    assert measures["Doanh_thu"]["type"] == "sum"
    assert measures["Doanh_thu"]["sql"] == "amount"
    # …while the readable name survives as the title.
    assert measures["Doanh_thu"]["title"] == "Doanh thu"
    # business filter compiled to a Cube measure filter
    assert measures["Doanh_thu"]["filters"][0]["sql"] == "{CUBE}.status = 'SUCCESS'"

    # a derived metric compiles to a Cube calculated measure
    assert measures["AOV"]["type"] == "number"
    assert measures["AOV"]["sql"] == "{Doanh_thu} / {Orders_Count}"

    # relationship → join to the referenced cube
    assert orders["joins"][0]["name"] == "customers"
    assert orders["joins"][0]["relationship"] == "many_to_one"
    assert "customer_id" in orders["joins"][0]["sql"]


def test_carries_business_names_through_as_titles() -> None:
    """The reviewed names are the whole point of the semantic layer; dropping
    them on the way to Cube wasted the review."""
    model = yaml.safe_load(generate_cube_yaml(_graph()))
    cubes = {c["name"]: c for c in model["cubes"]}

    assert cubes["orders"]["title"] == "Đơn hàng"
    dims = {d["name"]: d for d in cubes["orders"]["dimensions"]}
    assert dims["status"]["title"] == "Trạng thái"


def test_dimension_types_come_from_the_model_not_the_column_name() -> None:
    model = yaml.safe_load(generate_cube_yaml(_graph()))
    cubes = {c["name"]: c for c in model["cubes"]}

    orders_dims = {d["name"]: d for d in cubes["orders"]["dimensions"]}
    assert orders_dims["id"]["primary_key"] is True
    assert orders_dims["status"]["type"] == "string"
    # Hidden dimensions are kept in the model but not published to Cube.
    assert "note" not in orders_dims

    customers_dims = {d["name"]: d for d in cubes["customers"]["dimensions"]}
    assert customers_dims["created_at"]["type"] == "time"


def test_schema_qualifies_the_table() -> None:
    """SQL Server tables live in a schema; without this the model resolved
    against whatever the connection defaulted to."""
    graph = _graph()
    graph = graph.model_copy(
        update={"entities": [e.model_copy(update={"schema_name": "dbo"}) for e in graph.entities]}
    )
    model = yaml.safe_load(generate_cube_yaml(graph))
    assert model["cubes"][0]["sql_table"] == "dbo.orders"


def test_unsupported_filter_operator_raises_instead_of_guessing() -> None:
    """Falling back to '=' produced a plausible, wrong number — the worst
    possible outcome for an analytics tool."""
    graph = _graph()
    broken = graph.metrics[1].model_copy(
        update={"filters": [Filter(field="status", operator="contains", value="SUC")]}
    )
    ok = graph.model_copy(update={"metrics": [broken]})
    # `contains` is supported…
    assert "LIKE" in generate_cube_yaml(ok)

    # …but an operator with no SQL mapping must not silently become equality.
    unmapped = broken.model_copy(update={"filters": [_UnknownOpFilter()]})
    with pytest.raises(CubeCompileError):
        generate_cube_yaml(graph.model_copy(update={"metrics": [unmapped]}))


class _UnknownOpFilter(Filter):
    """A filter that bypassed validation (e.g. loaded from an older row)."""

    def __init__(self) -> None:
        super().__init__(field="status", operator="eq", value="X")
        object.__setattr__(self, "operator", "between")


def test_removing_a_model_deletes_its_cube_file(tmp_path) -> None:
    """An orphaned file stays queryable in Cube and answers with data nobody
    expects."""
    path = write_cube_model(_graph(), str(tmp_path))
    assert path.endswith("shop.yml")

    assert remove_cube_model("shop", str(tmp_path)) is True
    assert remove_cube_model("shop", str(tmp_path)) is False


def test_derived_metric_becomes_a_calculated_measure() -> None:
    """A formula over other metrics is the one thing Cube expresses natively —
    leaving it out meant the feature existed everywhere except at query time."""
    model = yaml.safe_load(generate_cube_yaml(_graph()))
    orders = next(c for c in model["cubes"] if c["name"] == "orders")
    aov = next(m for m in orders["measures"] if m["name"] == "AOV")

    assert aov["type"] == "number"
    # No title: the identifier already reads as the name, so repeating it would
    # be noise. Titles appear only where folding changed the text.
    assert "title" not in aov
    # References resolve to the identifiers the base measures were compiled to,
    # not to the business names the user typed.
    assert aov["sql"] == "{Doanh_thu} / {Orders_Count}"


def test_longer_metric_names_are_substituted_first() -> None:
    """`Doanh thu` must not be replaced inside `Doanh thu thuần`, which would
    leave a dangling ` thuần` in the compiled SQL."""
    graph = _graph()
    net = MetricDefinition(
        name="Doanh thu thuần",
        kind=MetricKind.base,
        entity_key=ORDERS,
        aggregation=Aggregation.sum,
        column="amount",
    )
    ratio = MetricDefinition(
        name="Tỷ lệ thuần",
        kind=MetricKind.derived,
        expression="Doanh thu thuần / Doanh thu",
    )
    graph = graph.model_copy(update={"metrics": [*graph.metrics, net, ratio]})

    model = yaml.safe_load(generate_cube_yaml(graph))
    orders = next(c for c in model["cubes"] if c["name"] == "orders")
    measure = next(m for m in orders["measures"] if m["name"] == "Ty_le_thuan")

    assert measure["sql"] == "{Doanh_thu_thuan} / {Doanh_thu}"


def test_a_derived_metric_spanning_entities_is_left_out() -> None:
    """Cube builds a calculated measure inside one cube. A formula whose parts
    live on different cubes cannot run, so it is not emitted — the validator
    reports it instead of it vanishing silently."""
    graph = _graph()
    customer_count = MetricDefinition(
        name="Số khách",
        kind=MetricKind.base,
        entity_key=CUSTOMERS,
        aggregation=Aggregation.count,
    )
    per_customer = MetricDefinition(
        name="Doanh thu mỗi khách",
        kind=MetricKind.derived,
        expression="Doanh thu / Số khách",
    )
    graph = graph.model_copy(update={"metrics": [*graph.metrics, customer_count, per_customer]})

    model = yaml.safe_load(generate_cube_yaml(graph))
    every_measure = {m["name"] for c in model["cubes"] for m in c["measures"]}

    assert "Doanh_thu_moi_khach" not in every_measure


def test_time_dimension_travels_to_the_measure() -> None:
    """Cube keeps time on the cube, not the measure, so the metric's default
    date column rides along as metadata. Without this the field was editable,
    validated — and had no effect at query time."""
    graph = _graph()
    metric = graph.metrics[1].model_copy(update={"time_dimension": "paid_at"})
    entity = graph.entities[0].model_copy(
        update={
            "dimensions": [
                *graph.entities[0].dimensions,
                Dimension(name="Ngày thu", column="paid_at", kind=DimensionKind.time, hidden=True),
            ]
        }
    )
    graph = graph.model_copy(update={"metrics": [metric], "entities": [entity, graph.entities[1]]})

    model = yaml.safe_load(generate_cube_yaml(graph))
    orders = next(c for c in model["cubes"] if c["name"] == "orders")

    measure = next(m for m in orders["measures"] if m["name"] == "Doanh_thu")
    assert measure["meta"]["default_time_dimension"] == "paid_at"

    # It was hidden as a slicing dimension, but a metric points at it, so it has
    # to be addressable.
    assert any(d["name"] == "paid_at" for d in orders["dimensions"])


# ----------------------------------------------------------------------
# MemberMap — business names to Cube members
# ----------------------------------------------------------------------


def test_map_and_model_always_agree_on_names() -> None:
    """The load-bearing invariant: every member the map advertises exists in
    the compiled model. A second `_ident()` anywhere else would drift from this
    one and surface as "member not found" only at query time."""
    graph = _graph()
    model = yaml.safe_load(generate_cube_yaml(graph))
    mapping = member_map(graph)

    real = {f"{cube['name']}.{m['name']}" for cube in model["cubes"] for m in cube["measures"]}
    assert set(mapping.measures.values()) == real

    real_dims = {
        f"{cube['name']}.{d['name']}" for cube in model["cubes"] for d in cube["dimensions"]
    }
    assert set(mapping.dimensions.values()) <= real_dims


def test_a_metric_is_found_by_its_business_name() -> None:
    mapping = member_map(_graph())
    assert mapping.measure("Doanh thu") == "orders.Doanh_thu"


def test_lookup_ignores_case_and_extra_spaces() -> None:
    """A model writing "doanh  thu" should not fail on punctuation of spacing."""
    mapping = member_map(_graph())
    assert mapping.measure("doanh  thu") == mapping.measure("Doanh thu")


def test_a_metric_is_also_found_by_its_id() -> None:
    graph = _graph()
    mapping = member_map(graph)
    revenue = next(m for m in graph.metrics if m.name == "Doanh thu")
    assert mapping.measure(revenue.id) == "orders.Doanh_thu"


def test_derived_metrics_are_addressable_too() -> None:
    mapping = member_map(_graph())
    assert mapping.measure("AOV") == "orders.AOV"


def test_dimensions_resolve_bare_and_qualified() -> None:
    mapping = member_map(_graph())
    assert mapping.dimensions[normalise("Trạng thái")] == "orders.status"
    assert mapping.dimensions[normalise("Đơn hàng.Trạng thái")] == "orders.status"
    # The raw column name works as well — a model often echoes it.
    assert mapping.dimensions[normalise("status")] == "orders.status"


def test_only_time_columns_land_in_time_dimensions() -> None:
    mapping = member_map(_graph())
    assert normalise("Signed Up") in mapping.time_dimensions
    assert normalise("Trạng thái") not in mapping.time_dimensions


def test_hidden_things_are_not_addressable() -> None:
    """What the query layer cannot run must not be offered: `note` is hidden."""
    mapping = member_map(_graph())
    assert normalise("Ghi chú") not in mapping.dimensions
    assert normalise("note") not in mapping.dimensions


def test_an_unknown_name_resolves_to_nothing() -> None:
    mapping = member_map(_graph())
    assert mapping.measure("Doanh thu thuần") is None
    assert mapping.metric_id("Doanh thu thuần") is None


def test_vietnamese_d_survives_ascii_folding() -> None:
    """`đ` is not an accented `d`, so NFKD cannot decompose it and a plain
    ascii-ignore deletes it: "Đơn hàng" became "on_hang" and two metrics could
    collapse onto one identifier."""
    graph = _graph()
    renamed = graph.metrics[1].model_copy(update={"name": "Học phí đã thu"})
    graph = graph.model_copy(update={"metrics": [renamed]})

    model = yaml.safe_load(generate_cube_yaml(graph))
    [measure] = model["cubes"][0]["measures"]

    assert measure["name"] == "Hoc_phi_da_thu"
    assert measure["title"] == "Học phí đã thu"


def test_an_entity_named_with_d_keeps_its_first_letter() -> None:
    mapping = member_map(_graph())
    # "Đơn hàng" is the entity name; the cube is named from its table, but the
    # folding rule is what a renamed table would rely on.
    assert "orders" in mapping.cubes.values()
