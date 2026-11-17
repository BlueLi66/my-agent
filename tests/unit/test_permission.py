import pytest

from my_agent.core.hooks import Allow, Ask, Deny
from my_agent.core.types import ToolCall
from my_agent.extensions.permission import PermissionExtension
from my_agent.tools.builtin import builtin_tools


@pytest.fixture
def permission():
    metadata = {tool.name: tool.side_effect for tool in builtin_tools()}
    return PermissionExtension({**metadata, "lookup": "read", "publish": "write", "job": "exec"})


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [
    "rm -rf /", 'rm -r -f "/"', "echo start && sudo tool", "SHUTDOWN /s", "reboot",
    "mkfs.ext4 drive", "dd of=file if=source", 'echo data > "/dev/sda"',
])
async def test_hard_deny_list_beats_cached_approval(permission, make_context, command):
    ctx = make_context()
    ctx.session.ext["permission"] = {"session_allowed": ["bash"]}
    decision = await permission.before_tool(ctx, ToolCall("id", "bash", {"command": command}))
    assert isinstance(decision, Deny)
    assert "deny list" in decision.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "decision_type"), [
    ("echo model delimiter del rm sudo reboot mkfs", Allow),
    ('echo "value & del sample; shutdown"', Allow),
    ("echo value && DEL sample.txt", Ask),
    ("rm -rf ./scratch", Ask),
    ("chmod 777 demo.txt", Ask),
    ("echo config > /etc/sample", Ask),
    ("python check.py", Allow),
])
async def test_shell_rules_use_command_boundaries(permission, make_context, command, decision_type):
    decision = await permission.before_tool(make_context(), ToolCall("id", "bash", {"command": command}))
    assert isinstance(decision, decision_type)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args"), [
    ("read_file", {"path": "../out.txt"}),
    ("write_file", {"path": "../out.txt"}),
    ("edit_file", {"path": "../out.txt"}),
    ("glob", {"pattern": "../*.txt"}),
    ("grep", {"glob": "../*.txt"}),
    ("grep", {"path": "../out.txt"}),
])
async def test_workspace_path_rejection_precedes_session_approval(permission, make_context, tool, args):
    ctx = make_context()
    ctx.session.ext["permission"] = {"session_allowed": [tool]}
    decision = await permission.before_tool(ctx, ToolCall("id", tool, args))
    assert isinstance(decision, Deny)
    assert "workspace" in decision.reason.casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "decision_type"), [
    ("lookup", {}, Allow),
    ("write_file", {"path": "inside.txt"}, Allow),
    ("publish", {}, Ask),
    ("job", {}, Ask),
    ("unregistered", {}, Deny),
])
async def test_permission_policy_uses_injected_tool_metadata(permission, make_context, tool, args, decision_type):
    assert isinstance(await permission.before_tool(make_context(), ToolCall("id", tool, args)), decision_type)
