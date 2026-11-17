from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .approval import ApprovalBroker
from .cancel import CancelToken
from .events import EventBus
from .session import Session, SessionStore
from .types import Message, ToolCall, ToolResult, ToolSpec

if TYPE_CHECKING:
    from my_agent.provider.base import Provider
    from my_agent.tools.registry import ToolRegistry

    from .hooks import HookDispatcher


@dataclass
class ModelRequest:
    system: str
    tools: list[ToolSpec]
    messages: list[Message]
    injections: list[Message] = field(default_factory=list)
    max_tokens: int = 8000
    model: str | None = None
    session_id: str = ""


@dataclass
class RunLimits:
    """Limit model-loop steps per run and token usage across the session."""

    max_turns: int = 50
    max_tokens_budget: int | None = None


@dataclass
class RunContext:
    session: Session
    provider: Provider
    tools: ToolRegistry
    hooks: HookDispatcher
    events: EventBus
    approval: ApprovalBroker
    store: SessionStore
    cancel: CancelToken
    workdir: Path
    system_prompt: str
    is_interactive: bool = True
    limits: RunLimits = field(default_factory=RunLimits)

    def fork_child(
        self,
        child_session: Session,
        *,
        tools: ToolRegistry,
        hooks: HookDispatcher,
        limits: RunLimits | None = None,
    ) -> RunContext:
        child_session.parent_id = self.session.id
        return replace(
            self,
            session=child_session,
            tools=tools,
            hooks=hooks,
            cancel=self.cancel.child(),
            limits=replace(limits if limits is not None else self.limits),
        )


def build_request(ctx: RunContext, max_tokens_override: int | None = None) -> ModelRequest:
    request = ModelRequest(
        ctx.system_prompt, ctx.tools.specs(), deepcopy(ctx.session.messages), session_id=ctx.session.id
    )
    if max_tokens_override is not None:
        request.max_tokens = max_tokens_override
    return request


def is_turn_boundary(messages: list[Message], index: int) -> bool:
    """A cut can retain a prefix only if all of its tool batches are closed."""
    return 0 <= index <= len(messages) and not validate_tool_pairs(messages[:index])


def validate_tool_pairs(messages: list[Message]) -> list[str]:
    """Report calls and results that cannot pair within an adjacent tool batch."""
    diagnostics: list[str] = []
    pending: dict[str, int] = {}

    for index, message in enumerate(messages):
        if message.role != "tool":
            for call_id, call_index in pending.items():
                diagnostics.append(f"Unpaired ToolCall {call_id!r} at message {call_index}")
            pending.clear()

        for block in message.blocks:
            if isinstance(block, ToolCall):
                if message.role != "assistant":
                    diagnostics.append(f"ToolCall {block.id!r} outside assistant message at {index}")
                elif block.id in pending:
                    diagnostics.append(f"Duplicate ToolCall {block.id!r} at message {index}")
                else:
                    pending[block.id] = index
            elif isinstance(block, ToolResult):
                if message.role != "tool" or block.call_id not in pending:
                    diagnostics.append(f"Unpaired ToolResult {block.call_id!r} at message {index}")
                else:
                    del pending[block.call_id]

    for call_id, call_index in pending.items():
        diagnostics.append(f"Unpaired ToolCall {call_id!r} at message {call_index}")
    return diagnostics
