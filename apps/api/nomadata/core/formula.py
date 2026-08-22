"""Reading a derived metric's formula — one definition, for everyone who reads one.

A formula is business names joined by arithmetic: ``Tổng doanh thu / Số đơn hàng``.
Three places need to know what it refers to — the validator, which decides
whether a model can be published; the Cube compiler, which decides which cube a
ratio lives in; and the metric suggester, which throws away proposals naming
things that do not exist.

They each had their own parser, and they disagreed. The compiler matched names
as substrings, so it read ``A Count (B)`` fine; the validator tokenised on
characters, so it split that name at the bracket and reported half of it as an
unknown metric. The same model was therefore valid in one place and broken in
another, and which one you believed depended on which code path you happened to
run. Two definitions of "valid" for one language is a bug waiting on a name with
punctuation in it, and we wrote one.

Substring matching, longest name first, is the definition. It is what the
compiler already did, it is what the web editor's chips do, and it does not care
what characters a business name contains.
"""

from __future__ import annotations

import re

#: Everything a formula may hold besides metric names.
_ARITHMETIC = re.compile(r"[+\-*/()]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def referenced_metrics(expression: str, names: list[str] | set[str]) -> list[str]:
    """Which of ``names`` the formula mentions.

    Longest first, so ``Doanh thu`` does not match inside ``Doanh thu thuần``
    and leave a fragment behind that resolves to nothing.
    """
    found: list[str] = []
    remaining = expression
    for candidate in sorted(names, key=len, reverse=True):
        if candidate and candidate in remaining:
            found.append(candidate)
            remaining = remaining.replace(candidate, " ")
    return found


def leftovers(expression: str, names: list[str] | set[str]) -> list[str]:
    """What the formula holds that is neither a known metric nor arithmetic.

    Each of these is a metric the model does not have. Cube compiles such a
    formula to nothing at all, so it would ship as a metric that is simply
    absent — which is why this is reported rather than tolerated.
    """
    remaining = expression
    for candidate in sorted(names, key=len, reverse=True):
        if candidate:
            remaining = remaining.replace(candidate, " ")

    unknown: list[str] = []
    for chunk in _ARITHMETIC.split(remaining):
        text = chunk.strip()
        if text and not _NUMBER.fullmatch(text):
            unknown.append(text)
    return unknown


def is_closed(expression: str, names: list[str] | set[str]) -> bool:
    """True when the formula names only metrics it is allowed to name."""
    return not leftovers(expression, names)
