"""AIProvider — provider-independent LLM capability surface.

Implementations live under ``nomadata.providers`` and are the ONLY modules
allowed to import a vendor SDK. The agent depends on this interface only and
never receives credentials directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

from nomadata.core.models import (
    ChatResponse,
    Message,
    ProviderCapabilities,
    ToolCallResponse,
    ToolSpec,
)

T = TypeVar("T", bound=BaseModel)


class AIProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier for this provider instance."""

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """What this provider can do — the agent depends on capabilities, not vendors."""

    @abstractmethod
    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        """Free-form chat completion."""

    @abstractmethod
    async def generate_structured(
        self, messages: list[Message], schema: type[T], **opts: Any
    ) -> T:
        """Return a validated instance of ``schema`` (used for semantic/query output)."""

    @abstractmethod
    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        """Let the model select and call agent tools."""
