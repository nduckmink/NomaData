"""Runtime registry for pluggable implementations.

The composition root (``nomadata.main``) registers concrete AIProvider /
DataSource / QueryEngine implementations here. Higher layers (e.g. the agent)
resolve them *by interface*, never by concrete class — that is what keeps the
system model-agnostic and data-source-agnostic.

In M0 the registry exists and is wired, but nothing is registered yet.
"""

from __future__ import annotations

from nomadata.core.errors import (
    DataSourceNotFoundError,
    ProviderNotFoundError,
    QueryEngineNotConfiguredError,
    SemanticModelNotConfiguredError,
)
from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.interfaces.data_source import DataSource
from nomadata.core.interfaces.query_engine import QueryEngine
from nomadata.core.interfaces.semantic_model import SemanticModel


class Registry:
    def __init__(self) -> None:
        self._providers: dict[str, AIProvider] = {}
        self._active_provider: AIProvider | None = None
        self._data_sources: dict[str, DataSource] = {}
        self._query_engine: QueryEngine | None = None
        self._semantic_model: SemanticModel | None = None

    # ---- AI providers ----
    def register_provider(self, name: str, provider: AIProvider) -> None:
        self._providers[name] = provider

    def get_provider(self, name: str) -> AIProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ProviderNotFoundError(name) from exc

    def provider_names(self) -> list[str]:
        return sorted(self._providers)

    def set_active_provider(self, provider: AIProvider | None) -> None:
        """Set (or clear) the currently active AI provider. Set live when the
        user saves AI config; cleared when the config is removed."""
        self._active_provider = provider

    def active_provider(self) -> AIProvider | None:
        """The active AI provider, or None if AI is unconfigured (heuristic only)."""
        return self._active_provider

    # ---- Data sources ----
    def register_data_source(self, name: str, source: DataSource) -> None:
        self._data_sources[name] = source

    def get_data_source(self, name: str) -> DataSource:
        try:
            return self._data_sources[name]
        except KeyError as exc:
            raise DataSourceNotFoundError(name) from exc

    def unregister_data_source(self, name: str) -> DataSource | None:
        """Remove and return a registered data source (None if absent)."""
        return self._data_sources.pop(name, None)

    def data_source_names(self) -> list[str]:
        return sorted(self._data_sources)

    # ---- Query engine ----
    def set_query_engine(self, engine: QueryEngine) -> None:
        self._query_engine = engine

    def get_query_engine(self) -> QueryEngine:
        if self._query_engine is None:
            raise QueryEngineNotConfiguredError("No query engine configured.")
        return self._query_engine

    # ---- Semantic model ----
    def set_semantic_model(self, model: SemanticModel) -> None:
        self._semantic_model = model

    def get_semantic_model(self) -> SemanticModel:
        if self._semantic_model is None:
            raise SemanticModelNotConfiguredError(
                "Semantic model unavailable — the app database is not connected."
            )
        return self._semantic_model


_registry = Registry()


def get_registry() -> Registry:
    """Return the process-wide registry."""
    return _registry
