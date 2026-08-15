"""VisualizationSelector — choose a chart *spec* for a result.

Returns a specification (type / x / y / series), never frontend code. The web
client renders the spec with ECharts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nomadata.core.models import QueryIntent, QueryResult, VisualizationSpec


class VisualizationSelector(ABC):
    @abstractmethod
    def select(self, result: QueryResult, intent: QueryIntent) -> VisualizationSpec:
        """Pick an appropriate visualization for a result given the user's intent."""
