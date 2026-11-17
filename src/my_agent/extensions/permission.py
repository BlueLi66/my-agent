import re
from collections.abc import Mapping
from pathlib import PureWindowsPath
from typing import Literal

from my_agent.core.approval import ApprovalResult
from my_agent.core.context import RunContext
from my_agent.core.hooks import Allow, Ask, Decision, Deny, Extension
from my_agent.core.types import ToolCall
from my_agent.tools.paths import safe_path, workspace_glob_pattern

SideEffect = Literal["read", "write", "exec"]

_COMMAND_SEGMENT = re.compile(r"""(?:[^;&|()\r\n'\"]|\"[^\"]*\"|'[^']*')+""")
_WORD = re.compile(r"""(?:[^\s'\"]|\"[^\"]*\"|'[^']*')+""")
_REDIRECT = re.compile(r"""\"[^\"]*\"|'[^']*'|>{1,2}\s*(?P<target>\"[^\"]+\"|'[^']+'|[^\s;&|()]+)""")
_HARD_DENY_COMMANDS = frozenset({"sudo", "shutdown", "reboot", "mkfs"})
_DESTRUCTIVE_COMMANDS = frozenset({"rm", "del", "erase", "rd", "rmdir", "format"})
_WORKSPACE_FILE_TOOLS = frozenset({"read_file", "write_file", "edit_file", "grep"})
_LOCAL_WRITE_TOOLS = frozenset({"write_file", "edit_file"})


def _commands(command: str) -> list[tuple[str, list[str]]]:
    commands = []
    for segment in _COMMAND_SEGMENT.findall(command):
        words = _WORD.findall(segment.strip().removeprefix("@"))
        if words:
            program = PureWindowsPath(words[0].strip("\"'")).name.casefold().removesuffix(".exe")
            commands.append((program, [word.strip("\"'") for word in words[1:]]))
    return commands


def _hard_denial(program: str, args: list[str]) -> str | None:
    if program in _HARD_DENY_COMMANDS or program.startswith("mkfs."):
        return program
    if program == "dd" and any(arg.casefold().startswith("if=") for arg in args):
        return "dd if="
    if program == "rm" and any(re.fullmatch(r"/+", arg) for arg in args):
        recursive = any(arg == "--recursive" or re.fullmatch(r"-[a-z]*r[a-z]*", arg) for arg in args)
        if recursive:
            return "recursive removal of the filesystem root"
    return None


class PermissionExtension(Extension):
    name = "permission"

    def __init__(self, side_effects: Mapping[str, SideEffect]) -> None:
        self._side_effects = dict(side_effects)

    async def before_tool(self, ctx: RunContext, call: ToolCall) -> Decision:
        command = call.args.get("command") if call.name == "bash" else None
        commands = _commands(command) if isinstance(command, str) else []
        redirects = [
            match["target"].strip("\"'") for match in _REDIRECT.finditer(command) if match["target"]
        ] if isinstance(command, str) else []
        for program, args in commands:
            if blocked := _hard_denial(program, args):
                return Deny(f"Blocked by shell deny list: {blocked}")
        if any(re.fullmatch(r"/dev/sda\d*", path) for path in redirects):
            return Deny("Blocked by shell deny list: redirection to /dev/sda")

        try:
            if call.name in _WORKSPACE_FILE_TOOLS and "path" in call.args:
                safe_path(call.args["path"], ctx.workdir)
            if call.name == "glob" and "pattern" in call.args:
                workspace_glob_pattern(call.args["pattern"])
            if call.name == "grep" and "glob" in call.args:
                workspace_glob_pattern(call.args["glob"])
        except (OSError, TypeError, ValueError) as error:
            return Deny(f"Workspace path rejected: {error}")

        side_effect = self._side_effects.get(call.name)
        if side_effect is None:
            return Deny(f"No permission metadata for tool {call.name!r}")

        needs_approval = side_effect in ("write", "exec") and call.name not in _LOCAL_WRITE_TOOLS
        if call.name == "bash" and side_effect == "exec":
            if not commands:
                return Deny("A shell command is required")
            needs_approval = any(
                program in _DESTRUCTIVE_COMMANDS or (program == "chmod" and "777" in args)
                for program, args in commands
            ) or any(path.startswith("/etc/") for path in redirects)
        session_allowed = ctx.session.ext.get(self.name, {}).get("session_allowed", ())
        if not needs_approval or call.name in session_allowed:
            return Allow()
        if isinstance(command, str):
            return Ask(f"Potentially destructive shell command\n{command}")
        return Ask(f"Allow {side_effect} tool {call.name!r}?")

    async def on_tool_approved(self, ctx: RunContext, call: ToolCall, result: ApprovalResult) -> None:
        if result.choice == "allow_session" and isinstance(await self.before_tool(ctx, call), Ask):
            state = ctx.session.ext.setdefault(self.name, {})
            state.setdefault("session_allowed", []).append(call.name)
