import pytest

from my_agent.core.context import RunLimits, validate_tool_pairs
from my_agent.core.errors import ModelError
from my_agent.core.hooks import Ask, Extension, StopDecision
from my_agent.core.loop import submit
from my_agent.core.types import Message, ModelResponse, TextBlock, ToolCall, ToolResult, Usage
from my_agent.tools.base import Tool


@pytest.mark.asyncio
async def test_submit_resets_run_limit_and_keeps_cumulative_session_turn(make_context):
    replies = [
        ModelResponse(Message("assistant", [TextBlock(text)]), Usage(), "end_turn")
        for text in ("first reply", "second reply")
    ]
    ctx = make_context(replies)
    ctx.limits = RunLimits(max_turns=1)

    await submit(ctx, "first question")
    assert ctx.session.status == "done"
    assert ctx.session.turn == 1
    await submit(ctx, "second question")

    assert ctx.session.status == "done"
    assert ctx.session.turn == 2
    assert [item.turn for item in ctx.store.checkpoints] == [1, 2]
    assert ctx.provider.requests[1].messages == [
        Message("user", [TextBlock("first question")]), replies[0].message,
        Message("user", [TextBlock("second question")]),
    ]


@pytest.mark.asyncio
async def test_limits_bound_stop_continuations(make_context):
    class KeepGoing(Extension):
        async def before_stop(self, ctx, resp):
            return StopDecision("continue", "try again")

    reply = ModelResponse(Message("assistant", [TextBlock("reply")]), Usage(), "end_turn")
    ctx = make_context([reply], extensions=[KeepGoing()])
    ctx.limits = RunLimits(max_turns=1)

    await submit(ctx, "start")

    assert len(ctx.provider.requests) == 1
    assert [cp.status for cp in ctx.store.checkpoints] == ["running"]
    assert [status.status for status in ctx.store.status_updates] == ["max_turns"]


@pytest.mark.asyncio
async def test_model_error_can_retry_once_then_fail_and_close(make_context):
    seen = []

    class Retry(Extension):
        async def on_model_error(self, ctx, error, req):
            seen.append((str(error), req.messages[-1].blocks[0].text))
            return True

        async def on_session_end(self, ctx):
            seen.append(ctx.session.status)

    ctx = make_context([ModelError("first"), ModelError("second")], extensions=[Retry()])

    with pytest.raises(ModelError, match="second"):
        await submit(ctx, "start")

    assert seen == [("first", "start"), ("second", "start"), "failed"]
    assert ctx.store.checkpoints == []
    assert [status.status for status in ctx.store.status_updates] == ["failed"]


@pytest.mark.asyncio
async def test_truncated_attempt_doubles_output_limit_without_appending_discarded_text(make_context):
    truncated = ModelResponse(Message("assistant", [TextBlock("discarded")]), Usage(2, 3), "max_tokens")
    final = ModelResponse(Message("assistant", [TextBlock("accepted")]), Usage(2, 1), "end_turn")
    ctx = make_context([truncated, final])

    await submit(ctx, "start")

    assert [request.max_tokens for request in ctx.provider.requests] == [8000, 16000]
    assert ctx.provider.requests[1].messages == [Message("user", [TextBlock("start")])]
    assert ctx.session.messages[-1] == final.message
    assert ctx.session.usage == Usage(4, 4)
    assert len(ctx.store.checkpoints) == 1


@pytest.mark.asyncio
async def test_default_broker_denies_request_and_keeps_tool_pair_closed(make_context):
    class RequestApproval(Extension):
        async def before_tool(self, ctx, call):
            return Ask("approval needed")

    executed = []

    async def tool(args, env):
        executed.append(True)
        return "ok"

    call = ModelResponse(Message("assistant", [ToolCall("id", "tool", {})]), Usage(), "tool_use")
    reply = ModelResponse(Message("assistant", [TextBlock("reply")]), Usage(), "end_turn")
    ctx = make_context([call, reply], tools=[Tool("tool", "Test tool", {}, tool)], extensions=[RequestApproval()])

    await submit(ctx, "start")

    assert not executed
    assert ctx.provider.requests[1].messages[-1] == Message("tool", [ToolResult("id", "approval needed", True)])
    assert ctx.session.status == "done"
    assert validate_tool_pairs(ctx.session.messages) == []
