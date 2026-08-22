"""One reading of a formula, for everyone who reads one.

The validator and the compiler each had their own parser and disagreed. The
compiler matched names as substrings and read `A Count — b` fine; the validator
tokenised on characters, split that name at the dash, and reported half of it as
an unknown metric. The same model was publishable or broken depending on which
code path you happened to run.
"""

from __future__ import annotations

import pytest

from nomadata.core.formula import is_closed, leftovers, referenced_metrics

NAMES = ["Doanh thu", "Doanh thu thuần", "Số đơn hàng"]


def test_the_longer_name_wins() -> None:
    """Otherwise "Doanh thu" matches inside "Doanh thu thuần" and leaves a
    fragment that belongs to nothing."""
    assert referenced_metrics("Doanh thu thuần / Số đơn hàng", NAMES) == [
        "Doanh thu thuần",
        "Số đơn hàng",
    ]


def test_arithmetic_and_numbers_are_not_names() -> None:
    assert leftovers("(Doanh thu thuần - Doanh thu) / Doanh thu * 100", NAMES) == []
    assert is_closed("(Doanh thu thuần - Doanh thu) / Doanh thu * 100", NAMES)


def test_anything_else_is_a_metric_the_model_does_not_have() -> None:
    """Cube compiles such a formula to nothing, so it would ship as a metric
    that is simply absent rather than as an error anybody sees."""
    assert leftovers("Tổng đã thu / Doanh thu", NAMES) == ["Tổng đã thu"]
    assert not is_closed("Tổng đã thu / Doanh thu", NAMES)


@pytest.mark.parametrize(
    "name",
    [
        "Hợp đồng doanh nghiệp Count — enterprise_contracts",
        "Tổng phí (sau thuế)",
        "Doanh thu 2024",
    ],
)
def test_punctuation_in_a_name_does_not_split_it(name: str) -> None:
    """A disambiguated name carries a separator, and a character-level tokeniser
    treated that separator as the end of the name."""
    names = [*NAMES, name]
    assert referenced_metrics(f"{name} / Số đơn hàng", names) == [name, "Số đơn hàng"]
    assert leftovers(f"{name} / Số đơn hàng", names) == []


def test_an_empty_formula_names_nothing() -> None:
    assert referenced_metrics("", NAMES) == []
    assert leftovers("", NAMES) == []
