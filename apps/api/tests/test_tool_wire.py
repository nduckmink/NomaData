"""Messages that can carry a tool result back to the model.

Without a call id on the request and a `tool_call_id` on the reply, the model
cannot tell which call is being answered — the agent stops after one tool call
and never sees a result. These tests pin the wire shape that makes turn two
possible.
"""

from __future__ import annotations

import json
from typing import Any

from nomadata.core.models import Message, Role, ToolCall
from nomadata.providers.openai_compatible import _wire


def test_a_plain_message_stays_plain() -> None:
    [item] = _wire([Message(role=Role.user, content="tổng học phí")])

    assert item == {"role": "user", "content": "tổng học phí"}


def test_assistant_turn_carries_its_tool_calls() -> None:
    message = Message(
        role=Role.assistant,
        tool_calls=[
            ToolCall(id="call_1", name="run_query", arguments={"measures": ["Học phí"]})
        ],
    )

    [item] = _wire([message])

    assert item["role"] == "assistant"
    # No prose in a tool-request turn: null, not "" — some endpoints reject "".
    assert item["content"] is None
    [call] = item["tool_calls"]
    assert call["id"] == "call_1"
    assert call["type"] == "function"
    assert call["function"]["name"] == "run_query"
    # Arguments travel as a JSON string, and survive non-ASCII intact.
    assert json.loads(call["function"]["arguments"]) == {"measures": ["Học phí"]}


def test_tool_reply_says_which_call_it_answers() -> None:
    [item] = _wire(
        [
            Message(
                role=Role.tool,
                content='{"rows": 4}',
                tool_call_id="call_1",
                name="run_query",
            )
        ]
    )

    assert item["tool_call_id"] == "call_1"
    assert item["name"] == "run_query"
    assert item["content"] == '{"rows": 4}'


def test_empty_tool_fields_are_dropped() -> None:
    """A null `tool_call_id` or an empty `tool_calls` is rejected by some
    OpenAI-compatible endpoints, so absent beats present-and-empty."""
    [item] = _wire([Message(role=Role.assistant, content="xong")])

    assert "tool_call_id" not in item
    assert "tool_calls" not in item
    assert "name" not in item


def test_a_full_two_turn_exchange_round_trips() -> None:
    """The shape an agent actually sends on its second turn."""
    conversation = [
        Message(role=Role.user, content="tổng học phí tháng này"),
        Message(
            role=Role.assistant,
            tool_calls=[ToolCall(id="call_1", name="run_query", arguments={})],
        ),
        Message(role=Role.tool, content="1284500000", tool_call_id="call_1"),
    ]

    wire = _wire(conversation)

    assert [m["role"] for m in wire] == ["user", "assistant", "tool"]
    assert wire[1]["tool_calls"][0]["id"] == wire[2]["tool_call_id"]


def test_tool_call_id_defaults_to_empty_not_missing() -> None:
    """A provider that issues no ids still produces a valid ToolCall."""
    call: Any = ToolCall(name="run_query")
    assert call.id == ""
