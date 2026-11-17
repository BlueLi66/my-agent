import asyncio
import json
import sys
from collections import deque

import pytest

from my_agent.core.approval import ApprovalResult
from my_agent.core.context import validate_tool_pairs
from my_agent.core.events import HookError
from my_agent.core.hooks import Extension, HookDispatcher
from my_agent.core.loop import submit
from my_agent.core.types import Message, ModelResponse, TextBlock, ToolCall, ToolResult, Usage
from my_agent.extensions.permission import PermissionExtension
from my_agent.extensions.user_hooks import UserHooksExtension
from my_agent.tools.base import Tool


def call_tools(*calls):
    return ModelResponse(Message("assistant", list(calls)), Usage(), "tool_use")


def reply():
    return ModelResponse(Message("assistant", [TextBlock("finished")]), Usage(), "end_turn")


class RecordingBroker:
    def __init__(self, choices):
        self.requests = []
        self._choices = deque(choices)

    async def request_approval(self, **request):
        self.requests.append(request)
        return ApprovalResult(self._choices.popleft())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choices", "asked", "ran", "session_allowed"),
    [
        (["allow_once", "deny"], ["first", "second"], [1], []),
        (["allow_session"], ["first"], [1, 2], ["publish"]),
        (["deny", "deny"], ["first", "second"], [], []),
    ],
)
async def test_approval_policy_in_tool_loop(make_context, choices, asked, ran, session_allowed):
    executed = []

    async def publish(args, env):
        executed.append(args["number"])
        return "published"

    tool = Tool("publish", "Test mutation", {}, publish, "write")
    ctx = make_context([
        call_tools(ToolCall("first", "publish", {"number": 1})), reply(),
        call_tools(ToolCall("second", "publish", {"number": 2})), reply(),
    ], tools=[tool], extensions=[PermissionExtension({tool.name: tool.side_effect})])
    ctx.approval = RecordingBroker(choices)

    await submit(ctx, "publish first")
    await submit(ctx, "publish second")

    assert ctx.session.status == "done"
    assert [request["tool_call"].id for request in ctx.approval.requests] == asked
    assert all(request["source"] == "permission" for request in ctx.approval.requests)
    assert executed == ran
    assert ctx.session.ext.get("permission", {}).get("session_allowed", []) == session_allowed
    assert validate_tool_pairs(ctx.session.messages) == []
    paired_results = [message.blocks[0] for message in ctx.session.messages if message.role == "tool"]
    assert [result.is_error for result in paired_results] == [1 not in ran, 2 not in ran]


@pytest.mark.asyncio
async def test_noninteractive_run_does_not_call_broker_or_execute_ask(make_context):
    executed = []

    async def tool_handler(args, env):
        executed.append(True)
        return "would have run"

    tool = Tool("bash", "Test command", {}, tool_handler, "exec")
    ctx = make_context([
        call_tools(ToolCall("id", "bash", {"command": "del sample.txt"})), reply(),
    ], tools=[tool], extensions=[PermissionExtension({tool.name: tool.side_effect})])
    ctx.is_interactive = False
    ctx.approval = RecordingBroker([])

    await submit(ctx, "remove sample")

    assert ctx.approval.requests == []
    assert executed == []
    assert ctx.provider.requests[1].messages[-1].blocks == [
        ToolResult("id", "Tool approval requires an interactive run.", True),
    ]
    assert ctx.session.status == "done"


@pytest.mark.asyncio
async def test_dispatcher_deny_on_before_tool_error_and_preserves_after_tool_result(make_context):
    executed = []

    class Unreliable(Extension):
        name = "unreliable"

        async def before_tool(self, ctx, call):
            if call.id == "blocked":
                raise RuntimeError("interceptor crashed")

        async def after_tool(self, ctx, call, result):
            raise RuntimeError("observer crashed")

    async def tool_handler(args, env):
        executed.append(args["value"])
        return "original result"

    ctx = make_context([
        call_tools(ToolCall("blocked", "echo", {"value": 1}), ToolCall("passed", "echo", {"value": 2})),
        reply(),
    ], tools=[Tool("echo", "Test echo", {}, tool_handler)], extensions=[Unreliable()])
    events = []
    ctx.events.subscribe(events.append)

    await submit(ctx, "run both")

    assert executed == [2]
    denied, allowed = ctx.provider.requests[1].messages[-1].blocks
    assert denied.is_error and "interceptor crashed" in denied.content
    assert allowed == ToolResult("passed", "original result")
    assert [event.hook for event in events if isinstance(event, HookError)] == ["before_tool", "after_tool"]
    assert ctx.store.checkpoints[-1].status == "done"


def write_hooks(ctx, config):
    directory = ctx.workdir / ".my-agent"
    directory.mkdir()
    (directory / "hooks.json").write_text(json.dumps(config), encoding="utf-8")
    ctx.hooks = HookDispatcher([UserHooksExtension.from_workdir(ctx.workdir)])


@pytest.mark.asyncio
@pytest.mark.parametrize(("phase", "fails_closed"), [("PreToolUse", True), ("PostToolUse", False)])
async def test_hook_script_timeout_is_isolated_and_cleans_child_process(make_context, phase, fails_closed):
    executed = []

    async def handler(args, env):
        executed.append(True)
        return "tool output"

    ctx = make_context([
        call_tools(ToolCall("id", "test_tool", {})), reply(),
    ], tools=[Tool("test_tool", "Test tool", {}, handler)])
    script = ctx.workdir / "slow_hook.py"
    script.write_text(
        "from pathlib import Path\nimport sys\nimport time\n"
        "sys.stdin.buffer.read()\nPath('started').touch()\ntime.sleep(2.5)\nPath('leaked').touch()\n",
        encoding="utf-8",
    )
    write_hooks(ctx, {phase: [{"command": f'"{sys.executable}" slow_hook.py', "timeout_s": 1.5}]})
    errors = []
    ctx.events.subscribe(lambda event: errors.append(event) if isinstance(event, HookError) else None)

    async with asyncio.timeout(8):
        await submit(ctx, "use tool")

    assert (ctx.workdir / "started").is_file()
    assert ctx.session.status == "done"
    result = ctx.provider.requests[1].messages[-1].blocks[0]
    assert result.is_error == fails_closed
    assert executed == ([] if fails_closed else [True])
    assert len(errors) == 1 and errors[0].extension == "user_hooks"
    assert "timed out after 1.5s" in errors[0].error
    assert errors[0].hook == ("before_tool" if fails_closed else "after_tool")
    if not fails_closed:
        assert result.content == "tool output"
    await asyncio.sleep(2.5)
    assert not (ctx.workdir / "leaked").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(("phase", "hook"), [("UserPromptSubmit", "on_user_input"), ("Stop", "before_stop")])
async def test_intercepting_user_scripts_with_nonzero_exit_fail_closed(make_context, phase, hook):
    ctx = make_context([reply()])
    write_hooks(ctx, {phase: [{"command": "exit 7"}]})
    errors = []
    ctx.events.subscribe(lambda event: errors.append(event) if isinstance(event, HookError) else None)

    outcome = await submit(ctx, "start")

    assert len(errors) == 1 and errors[0].hook == hook
    assert "status 7" in errors[0].error
    if phase == "UserPromptSubmit":
        assert "Input blocked" in outcome.direct_reply
        assert ctx.provider.requests == []
        assert ctx.session.messages == []
    else:
        assert ctx.session.status == "deferred"
        assert ctx.store.checkpoints[-1].status == "deferred"
