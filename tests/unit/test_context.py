import pytest

from my_agent.core.context import RunLimits, build_request, is_turn_boundary, validate_tool_pairs
from my_agent.core.hooks import HookDispatcher
from my_agent.core.session import Session
from my_agent.core.types import Message, TextBlock, ToolCall, ToolResult
from my_agent.tools.base import Tool
from my_agent.tools.registry import ToolRegistry


def test_turn_boundary_keeps_tool_batch_together():
    messages = [
        Message("user", [TextBlock("run")]),
        Message("assistant", [TextBlock("starting"), ToolCall("a", "tool", {}), ToolCall("b", "tool", {})]),
        Message("tool", [ToolResult("b", "second")]),
        Message("tool", [ToolResult("a", "first", is_error=True)]),
        Message("assistant", [TextBlock("finished")]),
    ]

    assert validate_tool_pairs(messages) == []
    assert [is_turn_boundary(messages, i) for i in range(len(messages) + 1)] == [
        True, True, False, False, True, True,
    ]
    assert not is_turn_boundary(messages, -1)
    assert not is_turn_boundary(messages, len(messages) + 1)
    assert is_turn_boundary([], 0)


def test_boundary_can_trim_an_unfinished_tail():
    messages = [
        Message("assistant", [ToolCall("closed", "tool", {})]),
        Message("tool", [ToolResult("closed", "done")]),
        Message("assistant", [ToolCall("in-flight", "tool", {})]),
    ]

    assert is_turn_boundary(messages, 2)
    assert not is_turn_boundary(messages, 3)
    assert validate_tool_pairs(messages) == ["Unpaired ToolCall 'in-flight' at message 2"]


def test_validate_tool_pairs_reports_unmatched_late_and_duplicate_blocks():
    messages = [
        Message("assistant", [ToolCall("ok", "tool", {}), ToolCall("missing", "tool", {})]),
        Message("tool", [ToolResult("ok", "one"), ToolResult("ok", "duplicate"), ToolResult("orphan", "x")]),
        Message("user", [TextBlock("new turn")]),
        Message("tool", [ToolResult("missing", "too late")]),
    ]

    assert validate_tool_pairs(messages) == [
        "Unpaired ToolResult 'ok' at message 1",
        "Unpaired ToolResult 'orphan' at message 1",
        "Unpaired ToolCall 'missing' at message 0",
        "Unpaired ToolResult 'missing' at message 3",
    ]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (Message("user", [ToolCall("a", "tool", {})]), "outside assistant message"),
        (Message("assistant", [ToolResult("a", "wrong role")]), "Unpaired ToolResult"),
        (Message("assistant", [ToolCall("a", "tool", {}), ToolCall("a", "tool", {})]), "Duplicate ToolCall"),
    ],
)
def test_validate_tool_pairs_rejects_wrong_roles_and_duplicate_call_ids(message, expected):
    assert any(expected in item for item in validate_tool_pairs([message]))


def test_request_owns_its_message_blocks_and_schemas(make_context):
    async def handle(args, env):
        return "ok"

    schema = {"type": "object", "properties": {"names": {"type": "array"}}}
    ctx = make_context(tools=[Tool("z", "Z", schema, handle), Tool("a", "A", {}, handle)])
    ctx.session.messages.append(Message("assistant", [ToolCall("id", "z", {"names": ["one"]})]))

    request = build_request(ctx, max_tokens_override=123)
    assert request.system == ctx.system_prompt
    assert request.max_tokens == 123
    assert [spec.name for spec in request.tools] == ["a", "z"]
    request.messages[0].blocks[0].args["names"].append("two")
    request.tools[1].schema["properties"].clear()
    request.injections.append(Message("user", [TextBlock("temporary")]))

    next_request = build_request(ctx)
    assert next_request.messages[0].blocks[0].args == {"names": ["one"]}
    assert next_request.tools[1].schema == schema
    assert next_request.injections == []
    assert next_request.max_tokens == 8000


def test_fork_child_inherits_services_with_separate_state_and_limits(make_context):
    ctx = make_context()
    ctx.limits = RunLimits(max_turns=7)
    child_session = Session("child")
    child_tools = ToolRegistry()
    child_hooks = HookDispatcher()

    child = ctx.fork_child(child_session, tools=child_tools, hooks=child_hooks)
    assert child.session is child_session
    assert child_session.parent_id == ctx.session.id
    assert child.tools is child_tools
    assert child.hooks is child_hooks
    for attr in ("provider", "events", "approval", "store"):
        assert getattr(child, attr) is getattr(ctx, attr)
    assert child.limits == ctx.limits
    child.limits.max_turns = 2
    child_session.ext["only-child"] = True
    assert ctx.limits.max_turns == 7
    assert ctx.session.ext == {}

    ctx.cancel.cancel("parent cancelled")
    assert child.cancel.is_cancelled
