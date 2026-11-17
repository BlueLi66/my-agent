from collections.abc import Iterable
from copy import deepcopy

from my_agent.core.errors import Cancelled
from my_agent.core.types import ToolCall, ToolResult, ToolSpec

from .base import Tool, ToolEnv


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

        try:
            content = await tool.handler(deepcopy(call.args), env)
        except Cancelled:
            raise
        except Exception as error:
            env.cancel.raise_if_cancelled()
            return ToolResult(call.id, f"{type(error).__name__}: {error}", is_error=True)

        env.cancel.raise_if_cancelled()
        return ToolResult(call.id, content)
