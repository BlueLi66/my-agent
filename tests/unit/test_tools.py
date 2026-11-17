import asyncio
import os
import subprocess
import sys

import pytest

from my_agent.core.cancel import CancelToken
from my_agent.core.errors import Cancelled
from my_agent.core.types import ToolCall, ToolResult
from my_agent.tools.base import Tool, ToolEnv
from my_agent.tools.builtin import builtin_tools
from my_agent.tools.paths import safe_path
from my_agent.tools.registry import ToolRegistry


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    return ToolEnv(path, "test", {}, CancelToken(), True)


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{}, {"count": True}, {"count": -1}, {"count": 1, "typo": 0}])
async def test_registry_rejects_invalid_arguments_before_handler(workspace, args):
    executed = []

    async def handle(args, env):
        executed.append(True)
        return "executed"

    schema = {
        "type": "object", "required": ["count"], "additionalProperties": False,
        "properties": {"count": {"type": "integer", "minimum": 0}},
    }
    tools = ToolRegistry([Tool("count", "Count", schema, handle)])
    result = await tools.execute(ToolCall("id", "count", args), workspace)
    assert result.is_error
    assert "args." in result.content
    assert not executed


@pytest.mark.asyncio
async def test_output_truncation_preserves_head_tail_and_budget(workspace):
    async def handle(args, env):
        return "START" + "x" * 100 + "END"

    tool = Tool("output", "Output", {}, handle, max_output_chars=35)
    result = await ToolRegistry([tool]).execute(ToolCall("out", "output", {}), workspace)
    assert not result.is_error
    assert len(result.content) == 35
    assert result.content.startswith("START")
    assert result.content.endswith("END")
    assert "[truncated]" in result.content


@pytest.mark.asyncio
async def test_builtin_file_tools_round_trip_and_search(workspace):
    builtins = builtin_tools()
    assert {tool.name: tool.side_effect for tool in builtins} == {
        "bash": "exec", "glob": "read", "grep": "read", "read_file": "read",
        "edit_file": "write", "write_file": "write",
    }
    tools = ToolRegistry(builtins)

    async def call(name, **args):
        result = await tools.execute(ToolCall(name, name, args), workspace)
        assert not result.is_error, result.content
        return result.content

    path = "pkg/demo.py"
    await call("write_file", path=path, content="a = 1\r\nprint(a)\r\na = 1\r\n")
    assert await call("read_file", path=path, offset=1, limit=1) == "print(a)\n... (more lines omitted)"
    await call("edit_file", path=path, old_text="a = 1\nprint(a)", new_text="a = 2\nprint(a)")
    assert (workspace.workdir / path).read_bytes() == b"a = 2\r\nprint(a)\r\na = 1\r\n"
    await call("write_file", path="root.py", content="a = 3\n")
    assert await call("glob", pattern="**/*.py") == "pkg/demo.py\nroot.py"
    assert await call("grep", pattern=r"a = [23]", glob="**/*.py") == "root.py:1: a = 3\npkg/demo.py:1: a = 2"


@pytest.mark.asyncio
async def test_file_tools_reject_workspace_escapes(workspace):
    outside = workspace.workdir.parent / "outside.txt"
    outside.write_text("sentinel", encoding="utf-8")
    tools = ToolRegistry(builtin_tools())
    for path in ("../outside.txt", str(outside)):
        with pytest.raises(ValueError, match="escapes workspace"):
            safe_path(path, workspace.workdir)
        for tool, args in (
            ("read_file", {"path": path}),
            ("write_file", {"path": path, "content": "changed"}),
            ("edit_file", {"path": path, "old_text": "sentinel", "new_text": "changed"}),
            ("grep", {"path": path, "pattern": "sentinel"}),
        ):
            result = await tools.execute(ToolCall("id", tool, args), workspace)
            assert result.is_error and "escapes workspace" in result.content
    result = await tools.execute(ToolCall("id", "glob", {"pattern": "../*.txt"}), workspace)
    assert result.is_error
    assert outside.read_text(encoding="utf-8") == "sentinel"


def test_safe_path_rejects_directory_link_to_outside(workspace):
    outside = workspace.workdir.parent / "outside"
    outside.mkdir()
    link = workspace.workdir / "link"
    if os.name == "nt":
        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes workspace"):
        safe_path(link / "sentinel.txt", workspace.workdir)


@pytest.mark.asyncio
async def test_bash_uses_platform_shell_and_replaces_invalid_utf8(workspace):
    if os.name == "nt":
        command = f'echo %COMSPEC% && "{sys.executable}" -c "import sys; sys.stdout.buffer.write(bytes([255, 65]))"'
    else:
        command = f'"{sys.executable}" -c "import sys; sys.stdout.buffer.write(bytes([255, 65]))"'
    tools = ToolRegistry(builtin_tools())
    result = await tools.execute(ToolCall("id", "bash", {"command": command}), workspace)
    assert not result.is_error, result.content
    assert result.content.endswith("\ufffdA")
    if os.name == "nt":
        assert "cmd.exe" in result.content.casefold()


@pytest.mark.asyncio
async def test_nonzero_command_is_a_tool_error(workspace):
    tool = ToolRegistry(builtin_tools())
    result = await tool.execute(ToolCall("id", "bash", {"command": "echo failure && exit 7"}), workspace)
    assert result.is_error
    assert "status 7" in result.content
    assert "failure" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["timeout", "cancel"])
async def test_shell_cleanup_stops_running_child(workspace, trigger):
    script = workspace.workdir / "slow.py"
    script.write_text(
        "from pathlib import Path\nimport time\n"
        "Path('started').touch()\ntime.sleep(2.5)\nPath('leaked').touch()\n",
        encoding="utf-8",
    )
    bash = next(tool for tool in builtin_tools() if tool.name == "bash")
    bash.timeout_s = 1.5 if trigger == "timeout" else 10
    tools = ToolRegistry([bash])
    command = f'"{sys.executable}" slow.py'
    task = asyncio.create_task(tools.execute(ToolCall("id", "bash", {"command": command}), workspace))

    async with asyncio.timeout(8):
        while not (workspace.workdir / "started").is_file() and not task.done():
            await asyncio.sleep(0.02)
        assert (workspace.workdir / "started").is_file()
        if trigger == "cancel":
            workspace.cancel.cancel("stop tool")
            with pytest.raises(Cancelled, match="stop tool"):
                await task
        else:
            assert await task == ToolResult("id", "Tool timed out after 1.5s", True)
    # A leaked child would have written after the registry returned.
    await asyncio.sleep(2.5)
    assert not (workspace.workdir / "leaked").exists()
