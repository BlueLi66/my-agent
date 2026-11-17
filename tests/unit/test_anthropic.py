import asyncio

import pytest
from anthropic import APIConnectionError, APIError

from my_agent.core.cancel import CancelToken
from my_agent.core.context import ModelRequest
from my_agent.core.errors import Cancelled, NonRetryableModelError, PromptTooLongError, RetryableModelError
from my_agent.core.events import EventBus, ModelRetry, TextDelta
from my_agent.core.types import Message, TextBlock, ToolCall, ToolResult, ToolSpec, Usage
from my_agent.provider import anthropic


def api_error(status, detail, kind="invalid_request_error"):
    error = APIError(detail, None, body={"type": "error", "error": {"type": kind, "message": detail}})
    error.status_code = status
    return error


def request():
    return ModelRequest("system", [], [Message("user", [TextBlock("begin")])], session_id="unit")


@pytest.mark.asyncio
async def test_wire_mapping_sorts_specs_and_merges_results_before_text(anthropic_client, api_reply):
    client = anthropic_client((["o", "k"], api_reply("ok", calls=[("two", "z", {"count": 1})])))
    provider = anthropic.AnthropicProvider(client, "primary")
    req = request()
    req.model = "override"
    req.tools = [ToolSpec("z", "Z", {"type": "object"}), ToolSpec("a", "A", {"type": "object"})]
    req.messages.extend([
        Message("assistant", [TextBlock("action"), ToolCall("one", "a", {"path": "a.py"})]),
        Message("tool", [ToolResult("one", "not found", True)]),
    ])
    req.injections.append(Message("user", [TextBlock("additional context")]))
    events = EventBus()
    observed = []
    events.subscribe(observed.append)

    result = await provider.complete(req, CancelToken(), events)

    assert [event for event in observed if isinstance(event, TextDelta)] == [TextDelta("unit", "o"), TextDelta("unit", "k")]
    assert result.text == "ok"
    assert result.tool_calls == [ToolCall("two", "z", {"count": 1})]
    assert result.usage == Usage(8, 2, 1, 3)
    wire = client.requests[0]
    assert wire["model"] == "override"
    assert [tool["name"] for tool in wire["tools"]] == ["a", "z"]
    assert req.tools[0].name == "z"
    assert wire["system"] == "system"
    assert wire["max_tokens"] == 8000
    assert wire["messages"][1]["content"][-1] == {
        "type": "tool_use", "id": "one", "name": "a", "input": {"path": "a.py"},
    }
    assert wire["messages"][2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "one", "content": "not found", "is_error": True},
        {"type": "text", "text": "additional context"},
    ]}
    assert req.messages[-1].role == "tool"


@pytest.mark.asyncio
async def test_retry_backoff_and_two_consecutive_overloads_select_fallback(monkeypatch, anthropic_client, api_reply):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(anthropic.asyncio, "sleep", sleep)
    monkeypatch.setattr(anthropic, "uniform", lambda low, high: high)
    # An SSE overload can be delivered after the successful HTTP response.
    sse_overload = api_error(200, "overloaded", "overloaded_error")
    client = anthropic_client(
        api_error(429, "limit", "rate_limit_error"), api_error(529, "overloaded"),
        ([sse_overload], None), (["ok"], api_reply("ok")),
        (["new"], api_reply("new")),
    )
    provider = anthropic.AnthropicProvider(client, "primary", fallback_model="fallback")

    result = await provider.complete(request(), CancelToken(), EventBus())
    next_result = await provider.complete(request(), CancelToken(), EventBus())

    assert (result.text, next_result.text) == ("ok", "new")
    assert [wire["model"] for wire in client.requests] == ["primary", "primary", "primary", "fallback", "primary"]
    assert delays == [0.625, 1.25, 2.5]
    assert client.closed_streams == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (api_error(400, "prompt is too long"), PromptTooLongError),
        (api_error(401, "invalid key"), NonRetryableModelError),
        (api_error(429, "limited"), RetryableModelError),
        (api_error(529, "overloaded"), RetryableModelError),
        (APIConnectionError(request=None), RetryableModelError),
    ],
)
async def test_error_classification_and_exhaustion(anthropic_client, error, expected):
    client = anthropic_client(error)
    provider = anthropic.AnthropicProvider(client, "primary", max_retries=0)
    with pytest.raises(expected):
        await provider.complete(request(), CancelToken(), EventBus())
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_stream_retry_marks_abandoned_text_and_returns_only_successful_reply(monkeypatch, anthropic_client, api_reply):
    monkeypatch.setattr(anthropic, "_retry_delay", lambda attempt: 0)
    client = anthropic_client(
        (["started", api_error(529, "overloaded")], None), (["finished"], api_reply("finished")),
    )
    provider = anthropic.AnthropicProvider(client, "primary", fallback_model="fallback")
    events = EventBus()
    received = []
    events.subscribe(received.append)
    result = await provider.complete(request(), CancelToken(), events)
    assert result.text == "finished"
    assert received == [
        TextDelta("unit", "started"), ModelRetry("unit", 0, status_code=529), TextDelta("unit", "finished"),
    ]
    assert len(client.requests) == client.closed_streams == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, 500, 502, 503, 504])
async def test_connection_and_server_errors_retry_with_backoff(monkeypatch, anthropic_client, api_reply, status):
    monkeypatch.setattr(anthropic, "uniform", lambda low, high: 0)
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(anthropic.asyncio, "sleep", sleep)
    error = APIConnectionError(request=None) if status is None else api_error(status, "temporary failure")
    client = anthropic_client(error, error, (["ok"], api_reply("ok")))
    provider = anthropic.AnthropicProvider(client, "primary", fallback_model="fallback")
    events = EventBus()
    received = []
    events.subscribe(received.append)

    assert (await provider.complete(request(), CancelToken(), events)).text == "ok"
    assert [wire["model"] for wire in client.requests] == ["primary"] * 3
    assert delays == [0.5, 1.0]
    assert received == [ModelRetry("unit", delay, status_code=status) for delay in delays] + [TextDelta("unit", "ok")]


@pytest.mark.asyncio
async def test_incomplete_stream_retries_and_respects_retry_limit(monkeypatch, anthropic_client, api_reply):
    monkeypatch.setattr(anthropic, "_retry_delay", lambda attempt: 0)
    incomplete = api_reply()
    incomplete.stop_reason = None
    client = anthropic_client(([], incomplete), ([], incomplete))
    provider = anthropic.AnthropicProvider(client, "primary", max_retries=1)
    events = EventBus()
    received = []
    events.subscribe(received.append)

    with pytest.raises(RetryableModelError, match="final stop reason"):
        await provider.complete(request(), CancelToken(), events)

    assert client.closed_streams == 2
    assert received == [ModelRetry("unit", 0)]


@pytest.mark.asyncio
async def test_cancel_closes_an_inflight_stream(anthropic_client):
    entered = asyncio.Event()

    async def blocked_stream():
        entered.set()
        await asyncio.Future()
        yield "never"

    client = anthropic_client((blocked_stream, None))
    provider = anthropic.AnthropicProvider(client, "primary")
    token = CancelToken()
    task = asyncio.create_task(provider.complete(request(), token, EventBus()))
    async with asyncio.timeout(3):
        await entered.wait()
        token.cancel("stop stream")
        with pytest.raises(Cancelled, match="stop stream"):
            await task
    assert client.closed_streams == 1
