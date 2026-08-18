"""Finding joins a schema never declared.

The SQL Server source here has 196 tables and 12 foreign keys. Without inferred
links, every question that crosses two tables is unanswerable — but a wrong link
silently pairs unrelated rows, so the rule has to refuse when unsure.
"""

from __future__ import annotations

from nomadata.core.models import (
    Dimension,
    DimensionKind,
    Entity,
    Relationship,
    SemanticGraph,
)
from nomadata.semantic.relationships import suggest_relationships

TRANSACTIONS = "app.transactions"
ENTERPRISES = "app.enterprises"
EMPLOYEES = "app.employee_labors"


def _entity(key: str, table: str, columns: list[str]) -> Entity:
    return Entity(
        key=key,
        name=table,
        table=table,
        schema_name="app",
        primary_key="id",
        dimensions=[Dimension(name=c, column=c, kind=DimensionKind.number) for c in columns],
    )


def _graph(*entities: Entity, links: list[Relationship] | None = None) -> SemanticGraph:
    return SemanticGraph(source_id="scp", entities=list(entities), relationships=links or [])


def test_matches_a_column_to_the_plural_table_it_names() -> None:
    graph = _graph(
        _entity(TRANSACTIONS, "transactions", ["enterprise_id", "request_amount"]),
        _entity(ENTERPRISES, "enterprises", ["name"]),
    )

    found = suggest_relationships(graph)

    assert len(found) == 1
    assert found[0].from_entity_key == TRANSACTIONS
    assert found[0].to_entity_key == ENTERPRISES
    assert found[0].from_column == "enterprise_id"
    assert found[0].to_column == "id"


def test_ignores_a_module_prefix_on_the_table_name() -> None:
    """`scp_hoc_phi` is referred to as `hoc_phi_id`, not `scp_hoc_phi_id`."""
    graph = _graph(
        _entity("app.payments", "payments", ["hoc_phi_id"]),
        _entity("app.scp_hoc_phi", "scp_hoc_phi", ["so_tien"]),
    )

    found = suggest_relationships(graph)

    assert [r.to_entity_key for r in found] == ["app.scp_hoc_phi"]


def test_refuses_when_two_tables_answer_to_the_same_name() -> None:
    """An ambiguous stem must not be resolved by picking one — a wrong join is
    worse than a missing one."""
    graph = _graph(
        _entity(TRANSACTIONS, "transactions", ["employee_id"]),
        _entity(EMPLOYEES, "employees", ["name"]),
        _entity("app.employee", "employee", ["name"]),
    )

    assert suggest_relationships(graph) == []


def test_does_not_repeat_a_link_that_already_exists() -> None:
    graph = _graph(
        _entity(TRANSACTIONS, "transactions", ["enterprise_id"]),
        _entity(ENTERPRISES, "enterprises", ["name"]),
        links=[
            Relationship(
                from_entity_key=TRANSACTIONS,
                to_entity_key=ENTERPRISES,
                from_column="enterprise_id",
                to_column="id",
            )
        ],
    )

    assert suggest_relationships(graph) == []


def test_ignores_columns_that_do_not_look_like_keys() -> None:
    graph = _graph(
        _entity(TRANSACTIONS, "transactions", ["request_amount", "status"]),
        _entity(ENTERPRISES, "enterprises", ["name"]),
    )

    assert suggest_relationships(graph) == []


def test_never_links_an_entity_to_itself() -> None:
    graph = _graph(_entity(ENTERPRISES, "enterprises", ["enterprise_id"]))
    assert suggest_relationships(graph) == []


def test_skips_hidden_entities() -> None:
    graph = _graph(
        _entity(TRANSACTIONS, "transactions", ["enterprise_id"]),
        _entity(ENTERPRISES, "enterprises", ["name"]).model_copy(update={"hidden": True}),
    )

    assert suggest_relationships(graph) == []
