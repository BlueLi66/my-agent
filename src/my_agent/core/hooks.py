from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .approval import ApprovalResult
from .errors import Cancelled, ModelError
from .events import HookError
from .types import ModelResponse, ToolCall, ToolResult

if TYPE_CHECKING:
    from my_agent.tools.base import Tool

    from .context import ModelRequest, RunContext


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Deny:
    reason: str


@dataclass(frozen=True)
class Ask:
    prompt: str


Decision = Allow | Deny | Ask


@dataclass(frozen=True)
class StopDecision:
    action: Literal["allow", "continue", "defer"]
    message: str | None = None


@dataclass(frozen=True)
class InputPass:
    text: str


@dataclass(frozen=True)
class InputHandled:
    reply: str


InputAction = InputPass | InputHandled


class Extension:
    name: str = "base"

    def tools(self) -> list[Tool]:
        return []

    def system_sections(self) -> list[str]:
        return []

    async def on_session_start(self, ctx: RunContext) -> None:
        pass

    async def on_user_input(self, ctx: RunContext, text: str) -> InputAction | None:
        return None

    async def before_model(self, ctx: RunContext, req: ModelRequest) -> None:
        pass

    async def after_model(self, ctx: RunContext, resp: ModelResponse) -> None:
        pass

    async def before_tool(self, ctx: RunContext, call: ToolCall) -> Decision | None:
        return None

    async def on_tool_approved(
        self, ctx: RunContext, call: ToolCall, result: ApprovalResult
    ) -> None:
        pass

    async def after_tool(
        self, ctx: RunContext, call: ToolCall, result: ToolResult
    ) -> ToolResult | None:
        return None

    async def before_stop(self, ctx: RunContext, resp: ModelResponse) -> StopDecision | None:
        return None

    async def on_model_error(self, ctx: RunContext, error: ModelError, req: ModelRequest) -> bool:
        return False

    async def on_session_end(self, ctx: RunContext) -> None:
        pass


async def _report_error(ctx: RunContext, extension: Extension, hook: str, error: Exception) -> None:
    logging.getLogger(__name__).warning("%s.%s failed: %s", extension.name, hook, error)
    await ctx.events.emit(HookError(ctx.session.id, extension.name, hook, str(error)))


async def _observe[T](
    ctx: RunContext, extension: Extension, hook: str, invoke: Callable[[], Awaitable[T]]
) -> T | None:
    try:
        return await invoke()
    except Cancelled:
        raise
    except Exception as error:
        await _report_error(ctx, extension, hook, error)
        return None


class HookDispatcher:
    def __init__(self, extensions: Iterable[Extension] = ()) -> None:
        self._extensions = tuple(extensions)

    def tools(self) -> list[Tool]:
        return [tool for ext in self._extensions for tool in ext.tools()]

    def system_sections(self) -> list[str]:
        return [section for ext in self._extensions for section in ext.system_sections()]

    async def on_session_start(self, ctx: RunContext) -> None:
        for ext in self._extensions:
            await _observe(ctx, ext, "on_session_start", lambda e=ext: e.on_session_start(ctx))

    async def on_user_input(self, ctx: RunContext, text: str) -> InputAction:
        for ext in self._extensions:
            try:
                action = await ext.on_user_input(ctx, text)
            except Cancelled:
                raise
            except Exception as error:
                await _report_error(ctx, ext, "on_user_input", error)
                return InputHandled(f"Input blocked by {ext.name}: {error}")
            if isinstance(action, InputHandled):
                return action
            if isinstance(action, InputPass):
                text = action.text
        return InputPass(text)

    async def before_model(self, ctx: RunContext, req: ModelRequest) -> None:
        for ext in self._extensions:
            await ext.before_model(ctx, req)

    async def after_model(self, ctx: RunContext, resp: ModelResponse) -> None:
        for ext in self._extensions:
            await _observe(ctx, ext, "after_model", lambda e=ext: e.after_model(ctx, resp))

    async def before_tool(self, ctx: RunContext, call: ToolCall) -> Allow | Deny | ApprovalResult:
        requests: list[tuple[Extension, Ask]] = []
        for ext in self._extensions:
            try:
                decision = await ext.before_tool(ctx, call)
            except Cancelled:
                raise
            except Exception as error:
                await _report_error(ctx, ext, "before_tool", error)
                return Deny(f"Tool blocked by {ext.name}: {error}")

            ctx.cancel.raise_if_cancelled()
            if isinstance(decision, Deny):
                return decision
            if isinstance(decision, Ask):
                requests.append((ext, decision))

        approved: ApprovalResult | None = None
        for ext, request in requests:
            if not ctx.is_interactive:
                return Deny("Tool approval requires an interactive run.")
            try:
                approval = await ctx.approval.request_approval(
                    session_id=ctx.session.id,
                    tool_call=call,
                    reason=request.prompt,
                    source=ext.name,
                    is_interactive=ctx.is_interactive,
                )
            except Cancelled:
                raise
            except Exception as error:
                await _report_error(ctx, ext, "request_approval", error)
                return Deny(f"Tool approval failed: {error}")

            ctx.cancel.raise_if_cancelled()
            if approval.choice not in ("allow_once", "allow_session"):
                return Deny(approval.reason or request.prompt)
            if approved is None or approval.choice == "allow_once":
                approved = approval

        if approved is None:
            return Allow()
        for ext in self._extensions:
            await _observe(
                ctx, ext, "on_tool_approved", lambda e=ext: e.on_tool_approved(ctx, call, approved)
            )
        return approved

    async def after_tool(self, ctx: RunContext, call: ToolCall, result: ToolResult) -> ToolResult:
        for ext in self._extensions:
            replacement = await _observe(
                ctx, ext, "after_tool", lambda e=ext, r=result: e.after_tool(ctx, call, r)
            )
            if replacement is not None:
                if replacement.call_id == call.id:
                    result = replacement
                else:
                    error = ValueError("Replacement ToolResult changed the call ID")
                    await _report_error(ctx, ext, "after_tool", error)
        return result

    async def before_stop(self, ctx: RunContext, resp: ModelResponse) -> StopDecision:
        for ext in self._extensions:
            try:
                decision = await ext.before_stop(ctx, resp)
            except Cancelled:
                raise
            except Exception as error:
                await _report_error(ctx, ext, "before_stop", error)
                return StopDecision("defer", str(error))

            if decision is not None and decision.action != "allow":
                return decision
        return StopDecision("allow")

    async def on_model_error(self, ctx: RunContext, error: ModelError, req: ModelRequest) -> bool:
        for ext in self._extensions:
            if await _observe(ctx, ext, "on_model_error", lambda e=ext: e.on_model_error(ctx, error, req)):
                return True
        return False

    async def on_session_end(self, ctx: RunContext) -> None:
        for ext in self._extensions:
            await _observe(ctx, ext, "on_session_end", lambda e=ext: e.on_session_end(ctx))
