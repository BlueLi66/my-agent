import asyncio
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from my_agent.core.errors import Cancelled
from my_agent.core.types import ToolCall, ToolResult, ToolSpec

from .base import Tool, ToolEnv


def _matches_type(value: Any, kind: str) -> bool:
    match kind:
        case "object":
            return isinstance(value, dict)
        case "array":
            return isinstance(value, list)
        case "string":
            return isinstance(value, str)
        case "integer":
            return type(value) is int
        case "number":
            return type(value) in (int, float)
        case "boolean":
            return isinstance(value, bool)
        case "null":
            return value is None
        case _:
            raise ValueError(f"Unsupported schema type: {kind}")


def _validate(value: Any, schema: dict[str, Any], path: str) -> None:
    kinds = schema.get("type")
    if isinstance(kinds, str):
        kinds = [kinds]
    if kinds and not any(_matches_type(value, kind) for kind in kinds):
        raise ValueError(f"{path} must be {' or '.join(kinds)}")

    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']!r}")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        raise ValueError(f"{path} must contain at least {schema['minLength']} characters")
    if type(value) in (int, float) and "minimum" in schema and value < schema["minimum"]:
        raise ValueError(f"{path} must be at least {schema['minimum']}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", ()):
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise ValueError(f"{path}.{key} is not accepted")
    elif isinstance(value, list) and isinstance(items := schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate(item, items, f"{path}[{index}]")


def _truncate(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    marker = "\n... [truncated] ...\n"
    if limit < len(marker) + 2:
        marker = "…"
    available = max(0, limit - len(marker))
    head = (available + 1) // 2
    tail = available - head
    return (content[:head] + marker + (content[-tail:] if tail else ""))[:limit]


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(tool.name, tool.description, deepcopy(tool.schema))
            for _, tool in sorted(self._tools.items())
        ]

    async def execute(self, call: ToolCall, env: ToolEnv) -> ToolResult:
        env.cancel.raise_if_cancelled()
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(call.id, f"Unknown tool: {call.name}", is_error=True)

        is_error = False
        try:
            _validate(call.args, {"type": "object", **tool.schema}, "args")
            async with asyncio.timeout(tool.timeout_s) as deadline:
                content = await env.cancel.run(lambda: tool.handler(deepcopy(call.args), env))
        except TimeoutError as error:
            is_error = True
            if deadline.expired():
                content = f"Tool timed out after {tool.timeout_s:g}s"
            else:
                content = f"{type(error).__name__}: {error}"
        except Cancelled:
            raise
        except Exception as error:
            is_error = True
            content = f"{type(error).__name__}: {error}"

        env.cancel.raise_if_cancelled()
        return ToolResult(call.id, _truncate(content, tool.max_output_chars), is_error)
