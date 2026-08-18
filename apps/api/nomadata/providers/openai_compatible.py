"""OpenAI-compatible AIProvider (OpenRouter / DeepSeek / OpenAI / local).

Talks the OpenAI ``/chat/completions`` wire format over httpx — no vendor SDK,
so this works against any endpoint that speaks it. This is the ONLY kind of
module allowed to hold provider credentials; higher layers depend on the
``AIProvider`` interface and never see the key.

Structured output uses JSON mode plus a schema-in-prompt, validated with
Pydantic and retried once on invalid output — not every OpenAI-compatible model
honors a strict JSON schema, so we verify rather than trust.
"""

from __future__ import annotations

import json
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from nomadata.core.errors import NomaDataError
from nomadata.core.interfaces.ai_provider import AIProvider
from nomadata.core.models import (
    ChatResponse,
    ConnectionState,
    ConnectionStatus,
    Message,
    ProviderCapabilities,
    Role,
    ToolCall,
    ToolCallResponse,
    ToolSpec,
)
from nomadata.logging import get_logger

T = TypeVar("T", bound=BaseModel)

log = get_logger()


class AIProviderError(NomaDataError):
    """The AI provider failed to respond or returned unusable output."""


class OpenAICompatibleProvider(AIProvider):
    def __init__(
        self,
        *,
        name: str = "openai_compatible",
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            chat=True,
            structured_output=True,
            tool_calling=True,
            streaming=False,
        )

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def verify(self) -> ConnectionStatus:
        """Check the endpoint + key without spending tokens where possible.

        Tries ``GET /models`` (cheap); if the endpoint doesn't expose it, falls
        back to a 1-token chat completion."""
        start = time.perf_counter()
        try:
            resp = await self._http().get("/models")
            if resp.status_code == 200:
                latency = (time.perf_counter() - start) * 1000
                return ConnectionStatus(state=ConnectionState.ok, latency_ms=latency)
            if resp.status_code in (404, 405):
                return await self._verify_via_chat(start)
            return ConnectionStatus(
                state=ConnectionState.error,
                message=f"AI endpoint returned {resp.status_code}: {resp.text[:200]}",
            )
        except httpx.HTTPError as exc:
            return ConnectionStatus(state=ConnectionState.error, message=str(exc))

    async def _verify_via_chat(self, start: float) -> ConnectionStatus:
        try:
            await self._post_chat(
                {
                    "model": self._model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }
            )
        except AIProviderError as exc:
            return ConnectionStatus(state=ConnectionState.error, message=str(exc))
        latency = (time.perf_counter() - start) * 1000
        return ConnectionStatus(state=ConnectionState.ok, latency_ms=latency)

    async def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._http().post("/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise AIProviderError(
                f"AI provider returned {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(f"AI provider request failed: {exc}") from exc
        data: dict[str, Any] = resp.json()
        return data

    async def chat(self, messages: list[Message], **opts: Any) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.model_dump() for m in messages],
            **opts,
        }
        data = await self._post_chat(payload)
        choice = data["choices"][0]["message"]["content"] or ""
        return ChatResponse(
            content=choice,
            model=data.get("model", self._model),
            usage=data.get("usage", {}) or {},
        )

    async def generate_structured(self, messages: list[Message], schema: type[T], **opts: Any) -> T:
        """Return a validated ``schema`` instance. JSON mode + schema-in-prompt,
        validated with Pydantic; one retry that feeds the error back to the model."""
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        primed: list[Message] = [
            Message(
                role=Role.system,
                content=(
                    "You output ONLY a single JSON object that validates against "
                    f"this JSON Schema:\n{schema_json}\n"
                    "No prose, no markdown fences — just the JSON object."
                ),
            ),
            *messages,
        ]

        last_error = ""
        for attempt in range(2):
            convo = list(primed)
            if attempt > 0:
                convo.append(
                    Message(
                        role=Role.user,
                        content=(
                            "Your previous reply did not validate: "
                            f"{last_error}\nReturn corrected JSON only."
                        ),
                    )
                )
            payload: dict[str, Any] = {
                "model": self._model,
                "messages": [m.model_dump() for m in convo],
                "response_format": {"type": "json_object"},
                **opts,
            }
            data = await self._post_chat(payload)
            content = data["choices"][0]["message"]["content"] or ""
            try:
                return schema.model_validate_json(_strip_fences(content))
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)[:500]
                log.warning("ai.structured.invalid", attempt=attempt, error=last_error)

        raise AIProviderError(f"AI provider did not return valid {schema.__name__}: {last_error}")

    async def tool_call(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> ToolCallResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.model_dump() for m in messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ],
            **opts,
        }
        data = await self._post_chat(payload)
        message = data["choices"][0]["message"]
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function", {})
            arguments = fn.get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            calls.append(ToolCall(name=fn.get("name", ""), arguments=arguments))
        return ToolCallResponse(tool_calls=calls, content=message.get("content"))


def _strip_fences(text: str) -> str:
    """Best-effort removal of ```json fences some models add despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped[: -len("```")]
    return stripped.strip()
