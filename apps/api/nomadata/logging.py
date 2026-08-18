"""Structured logging setup (structlog → JSON)."""

from __future__ import annotations

import logging

import structlog


def configure_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=numeric_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "nomadata") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def describe_exception(exc: BaseException) -> str:
    """A message that is never empty.

    ``str(TimeoutError())`` and ``str(CancelledError())`` are both ``""``, so a
    log line built from ``str(exc)`` reported a failure with no cause at all —
    the exact case that made an enrichment batch fail invisibly. The type name
    is always worth something, so fall back to it.
    """
    message = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {message}" if message else name
