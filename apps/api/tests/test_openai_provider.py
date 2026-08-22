"""OpenAI-compatible provider tests — hermetic via httpx.MockTransport."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from nomadata.core.models import ConnectionState, Message, Role
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
    resp = await provider.chat([Message(role=Role.user, content="hi")])
    assert resp.content == "hello"
    assert resp.usage == {"total_tokens": 3}
    await provider.aclose()


async def test_generate_structured_valid() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"model": "m", "choices": [{"message": {"content": '{"a": 1, "b": "x"}'}}]}
        )

    provider = _provider(httpx.MockTransport(handler))
    out = await provider.generate_structured([Message(role=Role.user, content="go")], _Shape)
    assert out == _Shape(a=1, b="x")
    await provider.aclose()


async def test_generate_structured_strips_fences() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        content = '```json\n{"a": 2, "b": "y"}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = _provider(httpx.MockTransport(handler))
    out = await provider.generate_structured([Message(role=Role.user, content="go")], _Shape)
    assert out == _Shape(a=2, b="y")
    await provider.aclose()


async def test_generate_structured_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        content = "not json" if calls["n"] == 1 else '{"a": 3, "b": "z"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = _provider(httpx.MockTransport(handler))
    out = await provider.generate_structured([Message(role=Role.user, content="go")], _Shape)
    assert out == _Shape(a=3, b="z")
    assert calls["n"] == 2
    await provider.aclose()


async def test_generate_structured_gives_up_after_retry() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "still not json"}}]})

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(AIProviderError):
        await provider.generate_structured([Message(role=Role.user, content="go")], _Shape)
    await provider.aclose()


async def test_http_error_becomes_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(AIProviderError):
        await provider.chat([Message(role=Role.user, content="hi")])
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


# ----------------------------------------------------------------------
# Retrying what is worth retrying
# ----------------------------------------------------------------------


async def test_a_rate_limit_is_tried_again(monkeypatch: Any) -> None:
    """The failures worth retrying are the ones that pass on their own. Losing
    a question to a 429 means a person watched a spinner for eight seconds and
    got an error for something that would have worked a second later."""
    monkeypatch.setattr("nomadata.providers.openai_compatible._RETRY_BACKOFF_S", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "hello"}}], "usage": {}}
        )

    provider = _provider(httpx.MockTransport(handler))
    resp = await provider.chat([Message(role=Role.user, content="hi")])

    assert resp.content == "hello"
    assert calls["n"] == 2
    await provider.aclose()


async def test_a_bad_request_is_not_tried_again() -> None:
    """The request is wrong, or the key is. Sending it twice more is three
    times the wait for the same answer."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad model")

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(AIProviderError):
        await provider.chat([Message(role=Role.user, content="hi")])

    assert calls["n"] == 1
    await provider.aclose()


async def test_a_timeout_is_not_tried_again() -> None:
    """The model was already given the time it was allowed; trying again spends
    it twice with somebody waiting."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("too slow", request=request)

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(AIProviderError):
        await provider.chat([Message(role=Role.user, content="hi")])

    assert calls["n"] == 1
    await provider.aclose()


async def test_it_gives_up_after_three_attempts(monkeypatch: Any) -> None:
    monkeypatch.setattr("nomadata.providers.openai_compatible._RETRY_BACKOFF_S", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="restarting")

    provider = _provider(httpx.MockTransport(handler))
    with pytest.raises(AIProviderError):
        await provider.chat([Message(role=Role.user, content="hi")])

    assert calls["n"] == 3
    await provider.aclose()
