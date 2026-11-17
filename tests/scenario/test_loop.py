import asyncio
from dataclasses import replace

import pytest

from my_agent.core.context import validate_tool_pairs
from my_agent.core.events import SessionEnded, ToolFinished, ToolStarted
from my_agent.core.hooks import Extension, InputHandled, InputPass, StopDecision
from my_agent.core.loop import submit
from my_agent.core.types import Message, ModelResponse, TextBlock, ToolCall, ToolResult, Usage
from my_agent.tools.base import Tool


@pytest.mark.asyncio
async def test_three_tool_rounds_close_in_order_before_final_reply(make_context):
    trace = []

    class Trace(Extension):
        async def on_session_start(self, ctx):
            trace.append("start")

        async def before_model(self, ctx, req):
            trace.append("before_model")
            req.injections.append(Message("user", [TextBlock("for this request only")]))

        async def after_model(self, ctx, resp):
            trace.append("after_model")

        async def before_tool(self, ctx, call):
            trace.append(f"before_{call.id}")

        async def after_tool(self, ctx, call, result):
            trace.append(f"after_{call.id}")
            return replace(result, content=f"checked {result.content}")

        async def before_stop(self, ctx, resp):
            trace.append("stop")
            return StopDecision("allow")

        async def on_session_end(self, ctx):
            trace.append("end")
            assert ctx.store.checkpoints[-1].status == "done"

    async def echo(args, env):
        trace.append(f"tool_{args['value']}")
        assert env.session_id == ctx.session.id
        assert env.workdir == ctx.workdir
        assert env.cancel is ctx.cancel
        return args["value"]

    rounds = [["a", "b"], ["c"], ["d"]]
    responses = [
        ModelResponse(
            Message("assistant", [ToolCall(value, "echo", {"value": value}) for value in batch]),
            Usage(2, 1),
            "tool_use",
        )
        for batch in rounds
    ]
    responses.append(ModelResponse(Message("assistant", [TextBlock("done")]), Usage(3, 2), "end_turn"))
    ctx = make_context(responses, tools=[Tool("echo", "Echo a value", {}, echo)], extensions=[Trace()])
    events = []
    ctx.events.subscribe(events.append)

    outcome = await submit(ctx, "echo all values")

    assert outcome.session is ctx.session
    assert outcome.direct_reply is None
    assert ctx.session.status == "done"
    assert ctx.session.turn == 4
    assert ctx.session.usage == Usage(9, 5)
    assert validate_tool_pairs(ctx.session.messages) == []
    assert [cp.turn for cp in ctx.store.checkpoints] == [1, 2, 3, 4]
    assert [cp.status for cp in ctx.store.checkpoints] == ["running", "running", "running", "done"]
    assert all(validate_tool_pairs(cp.messages) == [] for cp in ctx.store.checkpoints)
    assert ctx.store.status_updates == []

    expected_trace = ["start"]
    for batch, request in zip(rounds, ctx.provider.requests[1:], strict=True):
        expected_trace.extend(["before_model", "after_model"])
        for value in batch:
            expected_trace.extend([f"before_{value}", f"tool_{value}", f"after_{value}"])
        assert request.messages[-1] == Message("tool", [ToolResult(value, f"checked {value}") for value in batch])
        assert request.injections == [Message("user", [TextBlock("for this request only")])]
    expected_trace.extend(["before_model", "after_model", "stop", "end"])
    assert trace == expected_trace
    assert [message.role for message in ctx.session.messages] == ["user", "assistant", "tool"] + [
        "assistant", "tool", "assistant", "tool", "assistant",
    ]
    assert [event.call_id for event in events if isinstance(event, ToolStarted)] == list("abcd")
    assert [event.result.call_id for event in events if isinstance(event, ToolFinished)] == list("abcd")
    assert events[-1] == SessionEnded(ctx.session.id, "done")


@pytest.mark.asyncio
async def test_cancel_while_tool_is_running_saves_status_without_incomplete_checkpoint(make_context):
    entered_tool = asyncio.Event()
    release_tool = asyncio.Event()
    trace = []

    async def blocking_tool(args, env):
        def cleanup():
            trace.append("cleanup")
            release_tool.set()

        unsubscribe = env.cancel.on_cancel(cleanup)
        try:
            entered_tool.set()
            await release_tool.wait()
            env.cancel.raise_if_cancelled()
            return "done"
        finally:
            unsubscribe()

    class EndHook(Extension):
        async def on_session_end(self, ctx):
            trace.append(ctx.session.status)

    response = ModelResponse(Message("assistant", [ToolCall("cancel-me", "wait", {})]), Usage(), "tool_use")
    ctx = make_context([response], tools=[Tool("wait", "Wait for a signal", {}, blocking_tool)], extensions=[EndHook()])
    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        task = tasks.create_task(submit(ctx, "wait"))
        await entered_tool.wait()
        ctx.cancel.cancel("stop")

    assert task.result().session.status == "cancelled"
    assert trace == ["cleanup", "cancelled"]
    assert ctx.session.turn == 0
    assert ctx.store.checkpoints == []
    assert [status.status for status in ctx.store.status_updates] == ["cancelled"]
    assert len(ctx.provider.requests) == 1


@pytest.mark.asyncio
async def test_handled_input_skips_history_and_provider(make_context):
    class Trim(Extension):
        async def on_user_input(self, ctx, text):
            return InputPass(text.strip())

    class LocalCommand(Extension):
        async def on_user_input(self, ctx, text):
            if text == "/status":
                return InputHandled("local reply")

    ctx = make_context(extensions=[Trim(), LocalCommand()])
    outcome = await submit(ctx, " /status ")

    assert outcome.session is ctx.session
    assert outcome.direct_reply == "local reply"
    assert ctx.session.messages == []
    assert ctx.provider.requests == []
    assert ctx.store.checkpoints == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decisions", "expected_status", "checkpoints"),
    [
        ([StopDecision("allow")], "done", ["done"]),
        ([StopDecision("defer")], "deferred", ["deferred"]),
        ([StopDecision("continue", "finish the work"), StopDecision("allow")], "done", ["running", "done"]),
    ],
)
async def test_before_stop_decisions_checkpoint_completed_turn(make_context, decisions, expected_status, checkpoints):
    class StopHook(Extension):
        async def before_stop(self, ctx, resp):
            assert resp.text == "model reply"
            return decisions[ctx.session.turn]

    response = ModelResponse(Message("assistant", [TextBlock("model reply")]), Usage(), "end_turn")
    ctx = make_context([response] * len(decisions), extensions=[StopHook()])

    await submit(ctx, "start")

    assert ctx.session.status == expected_status
    assert ctx.session.turn == len(decisions)
    assert [item.status for item in ctx.store.checkpoints] == checkpoints
    assert ctx.store.checkpoints[-1].messages == ctx.session.messages
    assert len(ctx.provider.requests) == len(decisions)
    if len(decisions) == 2:
        feedback = Message("user", [TextBlock("finish the work")])
        assert ctx.provider.requests[1].messages[-1] == feedback
        assert ctx.store.checkpoints[0].messages[-1] == feedback
