"""OpenAI-compatible provider tests — hermetic via httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from nomadata.core.models import ConnectionState, Message
from nomadata.providers.openai_compatible import AIProviderError, OpenAICompatibleProvider


class _Shape(BaseModel):
    a: int
    b: str


def _provider(handler: httpx.MockTransport) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(base_url="http://ai.test/v1", api_key="k", model="m")
    # Inject a mock-backed client so no real network call is made.
    provider._client = httpx.AsyncClient(base_url="http://ai.test/v1", transport=handler)
    return provider


async def test_chat_returns_content() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"total_tokens": 3},
            },
        )

    provider = _provider(httpx.MockTransport(handler))
    resp = await provider.chat([Message(role="user", content="hi")])
    assert resp.content == "hello"
    assert resp.usage == {"total_tokens": 3}
    await provider.aclose()


async def test_generate_structured_valid() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"model": "m", "choices": [{"message": {"content": '{"a": 1, "b": "x"}'}}]}
        )

    provider = _provider(httpx.MockTransport(handler))
    out = await provider.generate_structured([Message(role="user", content="go")], _Shape)
    assert out == _Shape(a=1, b="x")
    await provider.aclose()


async def test_generate_structured_strips_fences() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        content = '```json\n{"a": 2, "b": "y"}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = _provider(httpx.MockTransport(handler))
    out = await provider.generate_structured([Message(role="user", content="go")], _Shape)
    assert out == _Shape(a=2, b="y")
    await provider.aclose()


async def test_generate_structured_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        content = "not json" if calls["n"] == 1 else '{"a": 3, "b": "z"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = _provider(httpx.MockTransport(handler))
    out = await provider.generate_structured([Message(role="user", content="go")], _Shape)
    assert out == _Shape(a=3, b="z")
    assert calls["n"] == 2
    await provider.aclose()


async def test_generate_structured_gives_up_after_retry() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "still not json"}}]})

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(AIProviderError):
        await provider.generate_structured([Message(role="user", content="go")], _Shape)
    await provider.aclose()


async def test_http_error_becomes_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(AIProviderError):
        await provider.chat([Message(role="user", content="hi")])
    await provider.aclose()


async def test_verify_ok_via_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": []})

    provider = _provider(httpx.MockTransport(handler))
    status = await provider.verify()
    assert status.state == ConnectionState.ok
    await provider.aclose()


async def test_verify_falls_back_to_chat_when_no_models_endpoint() -> None:
    seen = {"models": False, "chat": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            seen["models"] = True
            return httpx.Response(404)
        seen["chat"] = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider(httpx.MockTransport(handler))
    status = await provider.verify()
    assert status.state == ConnectionState.ok
    assert seen == {"models": True, "chat": True}
    await provider.aclose()


async def test_verify_reports_error_on_bad_key() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    provider = _provider(httpx.MockTransport(handler))
    status = await provider.verify()
    assert status.state == ConnectionState.error
    await provider.aclose()
