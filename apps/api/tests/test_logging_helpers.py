"""A failure must never be logged as nothing.

``str(TimeoutError())`` is the empty string, so an enrichment batch that timed
out reported ``{"error": "", "event": "semantic.enrich.batch_failed"}`` — a
warning that says a thing failed and refuses to say what.
"""

from __future__ import annotations

import asyncio

from nomadata.logging import describe_exception


def test_timeout_still_names_itself() -> None:
    assert str(TimeoutError()) == ""  # the trap
    assert describe_exception(TimeoutError()) == "TimeoutError"


def test_cancellation_still_names_itself() -> None:
    assert describe_exception(asyncio.CancelledError()) == "CancelledError"


def test_a_real_message_is_kept_and_typed() -> None:
    assert describe_exception(ValueError("bad column")) == "ValueError: bad column"


def test_whitespace_only_message_falls_back_to_the_type() -> None:
    assert describe_exception(RuntimeError("   ")) == "RuntimeError"
