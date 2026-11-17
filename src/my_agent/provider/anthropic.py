import asyncio
from copy import deepcopy
from random import uniform
from typing import Any, cast

from anthropic import APIConnectionError, APIError, AsyncAnthropic
from anthropic.types import Message as AnthropicMessage

from my_agent.core.cancel import CancelToken
from my_agent.core.context import ModelRequest
from my_agent.core.errors import ModelError, NonRetryableModelError, PromptTooLongError, RetryableModelError
from my_agent.core.events import EventBus, ModelRetry, TextDelta
from my_agent.core.types import Block, Message, ModelResponse, StopReason, TextBlock, ToolCall, ToolResult, Usage


def _encode_messages(messages: list[Message]) -> list[dict[str, Any]]:
    encoded: list[dict[str, Any]] = []
    for message in messages:
        blocks: list[dict[str, Any]] = []
        for block in message.blocks:
            match block:
                case TextBlock(text) if text:
                    blocks.append({"type": "text", "text": text})
                case ToolCall(call_id, name, args):
                    blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": deepcopy(args)})
                case ToolResult(call_id, content, is_error):
                    blocks.append({
                        "type": "tool_result", "tool_use_id": call_id, "content": content, "is_error": is_error,
                    })
        if not blocks:
            continue
        role = "assistant" if message.role == "assistant" else "user"
        if encoded and encoded[-1]["role"] == role:
            encoded[-1]["content"].extend(blocks)
        else:
            encoded.append({"role": role, "content": blocks})
    return encoded


def _decode_response(raw: AnthropicMessage) -> ModelResponse:
    reason = raw.stop_reason
    if reason is None:
        raise RetryableModelError("The Anthropic stream ended before its final stop reason.")
    if reason == "model_context_window_exceeded":
        raise PromptTooLongError("The model context window was exceeded.")
    if reason == "refusal":
        reason = "end_turn"
    if reason not in ("end_turn", "tool_use", "max_tokens", "stop_sequence"):
        raise NonRetryableModelError(f"Unsupported Anthropic stop reason: {reason}")

    blocks: list[Block] = []
    for block in raw.content:
        if block.type == "text":
            blocks.append(TextBlock(block.text))
        elif block.type == "tool_use" and reason != "max_tokens":
            # Truncation can leave a partially generated tool input in the SDK snapshot.
            if not isinstance(block.input, dict):
                raise NonRetryableModelError("Anthropic returned a tool call without an input object.")
            blocks.append(ToolCall(block.id, block.name, deepcopy(block.input)))
    if reason == "tool_use" and not any(isinstance(block, ToolCall) for block in blocks):
        raise NonRetryableModelError("Anthropic requested tool use without a client tool call.")

    usage = raw.usage
    return ModelResponse(
        Message("assistant", blocks),
        Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
        ),
        cast(StopReason, reason),
    )


def _classify_error(error: APIError) -> tuple[ModelError, int | None]:
    status = getattr(error, "status_code", None)
    body = error.body
    if isinstance(body, dict):
        body = body.get("error", body)
    kind = body.get("type") if isinstance(body, dict) else None
    if kind == "overloaded_error":
        status = 529
    elif kind == "rate_limit_error":
        status = 429

    details = f"{error} {getattr(error, 'error_code', '')} {body}".casefold()
    if any(phrase in details for phrase in (
        "prompt is too long", "prompt too long", "prompt_too_long", "context_length_exceeded",
        "context window exceeded", "maximum context length", "max_context_window",
        "model_context_window_exceeded",
    )):
        return PromptTooLongError("The model rejected the prompt's context length."), None

    if isinstance(error, APIConnectionError):
        return RetryableModelError("Could not complete the Anthropic connection."), None
    if status == 429 or (status is not None and status >= 500):
        return RetryableModelError(f"Anthropic API error (HTTP {status})."), status
    if status is not None and status >= 400:
        return NonRetryableModelError(f"Anthropic API error (HTTP {status})."), status
    return NonRetryableModelError("The Anthropic stream returned an API error."), None


def _retry_delay(attempt: int) -> float:
    base = min(0.5 * 2 ** attempt, 32.0)
    return base + uniform(0, base * 0.25)


class AnthropicProvider:
    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        *,
        fallback_model: str | None = None,
        max_retries: int = 3,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be nonnegative")
        self._client = client
        self._model = model
        self._fallback_model = fallback_model
        self._max_retries = max_retries

    async def complete(self, request: ModelRequest, cancel: CancelToken, events: EventBus) -> ModelResponse:
        return await cancel.run(lambda: self._complete(request, events))

    async def _complete(self, request: ModelRequest, events: EventBus) -> ModelResponse:
        params: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": _encode_messages([*request.messages, *request.injections]),
            "max_tokens": request.max_tokens,
        }
        if request.system:
            params["system"] = request.system
        if request.tools:
            params["tools"] = [
                {"name": spec.name, "description": spec.description, "input_schema": {"type": "object", **spec.schema}}
                for spec in sorted(request.tools, key=lambda spec: spec.name)
            ]

        consecutive_overloads = 0
        for attempt in range(self._max_retries + 1):
            try:
                async with self._client.messages.stream(**params) as stream:
                    async for delta in stream.text_stream:
                        await events.emit(TextDelta(request.session_id, delta))
                    return _decode_response(await stream.get_final_message())
            except (APIError, RetryableModelError) as error:
                mapped, status = _classify_error(error) if isinstance(error, APIError) else (error, None)
                if not isinstance(mapped, RetryableModelError) or attempt == self._max_retries:
                    if mapped is error:
                        raise
                    raise mapped from error
                consecutive_overloads = consecutive_overloads + 1 if status == 529 else 0
                if consecutive_overloads >= 2 and self._fallback_model:
                    params["model"] = self._fallback_model
                delay = _retry_delay(attempt)
                await events.emit(ModelRetry(request.session_id, delay, status_code=status))
                await asyncio.sleep(delay)

        raise AssertionError("Anthropic retry loop did not return or raise")
