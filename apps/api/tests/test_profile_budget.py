"""Which columns a build spends its profiling on.

Profiling is one query per column against the user's own database, so it has to
stop somewhere. It used to stop after 400 columns taken in catalogue order —
alphabetical by table — which on a 122-table source spent everything on the
`category_*` lookup tables and left the two tables the metrics live on with
nothing. The model then shipped knowing none of its own column values, and said
so only in a log line.
"""

from __future__ import annotations

import pytest

from nomadata.core.models import (
    ColumnInfo,
    ColumnProfile,
    DatabaseCatalog,
    GenerationJob,
    ProfileTarget,
    TableInfo,
)
from nomadata.semantic.jobs import _interleave_by_table, profile_dimension_candidates


def _catalog(shape: dict[str, int]) -> DatabaseCatalog:
    return DatabaseCatalog(
        source_id="scp",
        tables=[
            TableInfo(
                name=table,
                primary_key=["id"],
                columns=[ColumnInfo(name="id", data_type="int", is_primary_key=True)]
                + [ColumnInfo(name=f"c{i}", data_type="varchar") for i in range(columns)],
            )
            for table, columns in shape.items()
        ],
    )


class _Source:
    """Profiles instantly, and remembers the order it was asked in."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.asked: list[tuple[str, str]] = []
        self._fail = fail or set()

    async def profile(self, target: ProfileTarget) -> ColumnProfile:
        self.asked.append((target.table, target.column))
        if target.table in self._fail:
            raise RuntimeError("no")
        return ColumnProfile(table=target.table, column=target.column, distinct_count=3)


def test_every_table_is_reached_before_any_table_is_finished() -> None:
    """Round-robin: running out of time costs every table its rarest columns,
    rather than costing some tables everything."""
    targets = [
        ProfileTarget(table="a", column="a1"),
        ProfileTarget(table="a", column="a2"),
        ProfileTarget(table="a", column="a3"),
        ProfileTarget(table="z", column="z1"),
        ProfileTarget(table="z", column="z2"),
    ]

    ordered = [(t.table, t.column) for t in _interleave_by_table(targets)]

    assert ordered == [("a", "a1"), ("z", "z1"), ("a", "a2"), ("z", "z2"), ("a", "a3")]


def test_no_column_is_lost_by_the_reordering() -> None:
    targets = [ProfileTarget(table=t, column=f"c{i}") for t in ("a", "b", "c") for i in range(4)]

    ordered = _interleave_by_table(targets)

    assert sorted((t.table, t.column) for t in ordered) == sorted(
        (t.table, t.column) for t in targets
    )


@pytest.mark.asyncio
async def test_a_small_database_is_profiled_completely() -> None:
    source = _Source()
    catalog = _catalog({"transactions": 3, "enterprises": 2})

    profiles = await profile_dimension_candidates(source, catalog, None)  # type: ignore[arg-type]

    assert len(profiles) == 5
    assert {t for t, _ in source.asked} == {"transactions", "enterprises"}


@pytest.mark.asyncio
async def test_the_job_reports_what_was_covered() -> None:
    """A model built without values still works; it just knows less about what
    its own columns contain, and the reader has to be told which."""
    job = GenerationJob(id="j", source_id="scp")
    catalog = _catalog({"transactions": 2})

    await profile_dimension_candidates(_Source(), catalog, None, job)  # type: ignore[arg-type]

    assert job.profiled_columns == 2
    assert job.unprofiled_columns == 0


@pytest.mark.asyncio
async def test_a_column_that_cannot_be_profiled_is_skipped_not_fatal() -> None:
    """Profiling improves a draft; it must never block one."""
    catalog = _catalog({"transactions": 2, "broken": 2})

    profiles = await profile_dimension_candidates(
        _Source(fail={"broken"}),  # type: ignore[arg-type]
        catalog,
        None,
    )

    assert {t for t, _ in profiles} == {"transactions"}


@pytest.mark.asyncio
async def test_nothing_is_left_unprofiled_for_being_late() -> None:
    """A build runs once and its model is used until somebody rebuilds. Stopping
    early trades a few minutes now against a model that never learns what its
    own columns hold — so there is no global ceiling, only the per-column
    timeout that skips one pathological column and keeps the rest."""
    job = GenerationJob(id="j", source_id="scp")
    catalog = _catalog({f"t{i}": 20 for i in range(40)})
    source = _Source()

    profiles = await profile_dimension_candidates(source, catalog, None, job)  # type: ignore[arg-type]

    assert len(profiles) == 800
    assert job.profile_total == 800
    assert job.unprofiled_columns == 0


@pytest.mark.asyncio
async def test_a_failure_is_counted_so_the_gap_is_visible() -> None:
    job = GenerationJob(id="j", source_id="scp")
    catalog = _catalog({"transactions": 2, "broken": 3})

    await profile_dimension_candidates(_Source(fail={"broken"}), catalog, None, job)  # type: ignore[arg-type]

    assert job.profile_total == 5
    assert job.profiled_columns == 2
    assert job.unprofiled_columns == 3
