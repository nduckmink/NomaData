"""NomaData error hierarchy."""

from __future__ import annotations


class NomaDataError(Exception):
    """Base class for all NomaData errors."""


class ConfigurationError(NomaDataError):
    """Invalid or missing configuration."""


class ProviderNotFoundError(NomaDataError):
    """Requested AI provider is not registered."""

    def __init__(self, name: str) -> None:
        super().__init__(f"AI provider not registered: {name!r}")
        self.name = name


class DataSourceNotFoundError(NomaDataError):
    """Requested data source is not registered."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Data source not registered: {name!r}")
        self.name = name


class QueryEngineNotConfiguredError(NomaDataError):
    """No query engine has been configured."""


class DataConnectionError(NomaDataError):
    """A data source failed to connect or respond."""
