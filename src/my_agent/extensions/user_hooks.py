import asyncio
import json
import logging
import math
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

from my_agent.core.context import RunContext
from my_agent.core.errors import Cancelled
from my_agent.core.events import HookError
from my_agent.core.hooks import Decision, Deny, Extension, InputAction, InputHandled, InputPass, StopDecision
from my_agent.core.types import ModelResponse, ToolCall, ToolResult
from my_agent.tools.process import run_shell

_EVENTS = frozenset({"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"})
_TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse"})


class HookConfigError(ValueError):
    """The workspace hook configuration is invalid."""


@dataclass(frozen=True)
class ScriptHook:
    command: str
    timeout_s: float
    matcher: str | None = None


class UserHooksExtension(Extension):
    """Run ordered workspace scripts with a JSON lifecycle payload on stdin.

    The file maps event names to lists of {command, timeout_s, matcher} entries.
    timeout_s defaults to 10 seconds. matcher is an optional tool-name glob.
    Exit zero passes. A JSON object on stdout can rewrite or handle input,
    deny a tool, or select a stop action. PostToolUse observes the tool result.
    """

    name = "user_hooks"

    def __init__(self, hooks: dict[str, tuple[ScriptHook, ...]] | None = None) -> None:
        self._hooks = dict(hooks or {})

    @classmethod
    def from_workdir(cls, workdir: Path) -> "UserHooksExtension":
        directory = workdir / ".my-agent"
        path = next((file for name in ("hooks.yaml", "hooks.yml", "hooks.json")
                     if (file := directory / name).is_file()), None)
        if path is None:
            return cls()
        try:
            source = path.read_text(encoding="utf-8-sig")
            config = json.loads(source) if path.suffix == ".json" else yaml.safe_load(source)
            return cls(_parse_config(config))
        except (OSError, ValueError, yaml.YAMLError) as error:
            raise HookConfigError(f"Invalid hook configuration in {path}: {error}") from error

    def _matching(self, event: str, tool_name: str = "") -> Iterator[ScriptHook]:
        for hook in self._hooks.get(event, ()):
            if hook.matcher is None or fnmatchcase(tool_name, hook.matcher):
                yield hook

    async def on_user_input(self, ctx: RunContext, text: str) -> InputAction | None:
        original = text
        for hook in self._matching("UserPromptSubmit"):
            output = await _execute(ctx, "UserPromptSubmit", hook, {"text": text})
            if "reply" in output:
                return InputHandled(_text(output, "reply"))
            if "text" in output:
                text = _text(output, "text")
        return InputPass(text) if text != original else None

    async def before_tool(self, ctx: RunContext, call: ToolCall) -> Decision | None:
        for hook in self._matching("PreToolUse", call.name):
            output = await _execute(ctx, "PreToolUse", hook, {"tool_call": asdict(call)})
            decision = output.get("decision", "allow")
            if decision == "deny":
                return Deny(_text(output, "reason", "Blocked by PreToolUse"))
            if decision != "allow":
                raise ValueError("PreToolUse decision must be allow or deny")
        return None

    async def after_tool(self, ctx: RunContext, call: ToolCall, result: ToolResult) -> None:
        for hook in self._matching("PostToolUse", call.name):
            try:
                await _execute(ctx, "PostToolUse", hook, {"tool_call": asdict(call), "tool_result": asdict(result)})
            except Cancelled:
                raise
            except Exception as error:
                logging.getLogger(__name__).warning("PostToolUse failed: %s", error)
                await ctx.events.emit(HookError(ctx.session.id, self.name, "after_tool", str(error)))

    async def before_stop(self, ctx: RunContext, resp: ModelResponse) -> StopDecision | None:
        for hook in self._matching("Stop"):
            output = await _execute(ctx, "Stop", hook, {"text": resp.text, "stop_reason": resp.stop_reason})
            action = output.get("action", "allow")
            if action not in ("allow", "continue", "defer"):
                raise ValueError("Stop action must be allow, continue, or defer")
            if action != "allow":
                message = _text(output, "message") if "message" in output else None
                return StopDecision(action, message)
        return None


def _parse_config(config: Any) -> dict[str, tuple[ScriptHook, ...]]:
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise HookConfigError("Expected a mapping of events to hook lists")
    if set(config) == {"hooks"}:
        config = config["hooks"]
        if not isinstance(config, dict):
            raise HookConfigError("Expected a mapping under hooks")
    hooks = {}
    for event, entries in config.items():
        if event not in _EVENTS or not isinstance(entries, list):
            raise HookConfigError(f"Expected a hook list for a supported event, got {event!r}")
        scripts = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.keys() - {"command", "timeout_s", "matcher"}:
                raise HookConfigError(f"Invalid {event} hook entry")
            command = entry.get("command")
            if not isinstance(command, str) or not command.strip():
                raise HookConfigError(f"{event} requires a nonempty command")
            timeout_s = entry.get("timeout_s", 10.0)
            if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
                raise HookConfigError(f"{event} timeout_s must be positive and finite")
            matcher = entry.get("matcher")
            if matcher is not None and (event not in _TOOL_EVENTS or not isinstance(matcher, str) or not matcher):
                raise HookConfigError("matcher must be a tool-name glob on PreToolUse or PostToolUse")
            scripts.append(ScriptHook(command, timeout_s, matcher))
        hooks[event] = tuple(scripts)
    return hooks


async def _execute(ctx: RunContext, event: str, hook: ScriptHook, fields: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({
        "event": event, "session_id": ctx.session.id, "turn": ctx.session.turn,
        "workdir": str(ctx.workdir), **fields,
    }, ensure_ascii=False).encode("utf-8")
    try:
        async with asyncio.timeout(hook.timeout_s):
            result = await ctx.cancel.run(lambda: run_shell(hook.command, ctx.workdir, input_data=payload))
    except TimeoutError as error:
        raise TimeoutError(f"{event} script timed out after {hook.timeout_s:g}s") from error
    if result.returncode:
        raise RuntimeError(f"{event} script exited with status {result.returncode}")
    stdout = result.stdout.strip()
    if event == "PostToolUse" or not stdout.startswith("{"):
        return {}
    data = json.loads(stdout)
    if not isinstance(data, dict):
        raise ValueError(f"{event} script output must be a JSON object")
    return data


def _text(output: dict[str, Any], key: str, default: str = "") -> str:
    value = output.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"Hook field {key!r} must be text")
    return value
