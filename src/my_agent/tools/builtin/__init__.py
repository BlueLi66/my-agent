from collections.abc import Awaitable, Callable
from typing import Any, Literal

from ..base import Tool, ToolEnv
from .files import edit_file, glob_files, grep_files, read_file, write_file
from .shell import run_bash


def _tool(
    name: str,
    description: str,
    handler: Callable[[dict[str, Any], ToolEnv], Awaitable[str]],
    properties: dict[str, Any],
    required: list[str],
    side_effect: Literal["read", "write", "exec"] = "read",
) -> Tool:
    return Tool(name, description, {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }, handler, side_effect)


def builtin_tools() -> list[Tool]:
    string = {"type": "string"}
    nonempty = {"type": "string", "minLength": 1}
    return [
        _tool("bash", "Run a command from the workspace using cmd.exe on Windows or sh on POSIX.",
              run_bash, {"command": nonempty}, ["command"], "exec"),
        _tool("read_file", "Read a UTF-8 file, optionally with a zero-based line offset and a line limit.",
              read_file, {"path": nonempty, "offset": {"type": "integer", "minimum": 0},
                          "limit": {"type": "integer", "minimum": 1}}, ["path"]),
        _tool("write_file", "Write UTF-8 content to a file, creating parent directories if needed.",
              write_file, {"path": nonempty, "content": string}, ["path", "content"], "write"),
        _tool("edit_file", "Replace the first occurrence of exact text in a UTF-8 file.",
              edit_file, {"path": nonempty, "old_text": nonempty, "new_text": string},
              ["path", "old_text", "new_text"], "write"),
        _tool("glob", "Find files by workspace-relative glob pattern. ** searches recursively.",
              glob_files, {"pattern": nonempty}, ["pattern"]),
        _tool("grep", "Search file contents with a regular expression and return file:line matches.",
              grep_files, {"pattern": nonempty, "path": nonempty, "glob": nonempty}, ["pattern"]),
    ]
