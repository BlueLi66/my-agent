import json
import sys
from textwrap import dedent

import pytest
import yaml

from my_agent.core.hooks import Deny, InputPass, StopDecision
from my_agent.core.types import Message, ModelResponse, TextBlock, ToolCall, ToolResult, Usage
from my_agent.extensions.user_hooks import HookConfigError, UserHooksExtension


def test_missing_hook_configuration_loads_empty(make_context):
    ctx = make_context()
    assert isinstance(UserHooksExtension.from_workdir(ctx.workdir), UserHooksExtension)


def test_invalid_hook_configuration_reports_the_event(make_context):
    directory = make_context().workdir / ".my-agent"
    directory.mkdir()
    (directory / "hooks.yaml").write_text(
        "PreToolUse:\n  - command: echo okay\n    timeout_s: false\n", encoding="utf-8",
    )
    with pytest.raises(HookConfigError, match="PreToolUse timeout_s"):
        UserHooksExtension.from_workdir(directory.parent)


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["yaml", "json"])
async def test_user_scripts_receive_lifecycle_payloads_and_return_decisions(make_context, suffix):
    ctx = make_context()
    directory = ctx.workdir / ".my-agent"
    directory.mkdir()
    script = ctx.workdir / "probe.py"
    script.write_text(dedent('''
        import json
        import sys
        from pathlib import Path

        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        with Path("events.jsonl").open("a", encoding="utf-8") as trace:
            trace.write(json.dumps(payload) + "\\n")
        match payload["event"]:
            case "UserPromptSubmit":
                print(json.dumps({"text": payload["text"] + " updated"}))
            case "PreToolUse":
                print(json.dumps({"decision": "deny", "reason": "blocked by script"}))
            case "PostToolUse":
                sys.stdout.buffer.write(bytes([255]))
            case "Stop":
                print(json.dumps({"action": "continue", "message": "one more step"}))
    '''), encoding="utf-8")
    command = f'"{sys.executable}" probe.py'
    config = {
        "UserPromptSubmit": [{"command": command}],
        "PreToolUse": [{"command": command, "matcher": "write_*"}],
        "PostToolUse": [{"command": command}],
        "Stop": [{"command": command}],
    }
    source = yaml.safe_dump(config) if suffix == "yaml" else json.dumps({"hooks": config})
    (directory / f"hooks.{suffix}").write_text(source, encoding="utf-8-sig")
    extension = UserHooksExtension.from_workdir(ctx.workdir)
    call = ToolCall("id", "write_file", {"path": "a.txt"})
    response = ModelResponse(Message("assistant", [TextBlock("final")]), Usage(), "end_turn")
    events = []
    ctx.events.subscribe(events.append)

    assert await extension.on_user_input(ctx, "输入") == InputPass("输入 updated")
    assert await extension.before_tool(ctx, ToolCall("other", "read_file", {})) is None
    assert await extension.before_tool(ctx, call) == Deny("blocked by script")
    await extension.after_tool(ctx, call, ToolResult("id", "completed"))
    assert await extension.before_stop(ctx, response) == StopDecision("continue", "one more step")

    records = [json.loads(line) for line in (ctx.workdir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [item["event"] for item in records] == list(config)
    assert all(item["session_id"] == ctx.session.id for item in records)
    assert records[1]["tool_call"] == {"id": "id", "name": "write_file", "args": {"path": "a.txt"}}
    assert records[2]["tool_result"]["content"] == "completed"
    assert records[3]["text"] == "final"
    assert events == []
