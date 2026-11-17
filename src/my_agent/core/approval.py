from dataclasses import dataclass
from typing import Literal, Protocol

from .types import ToolCall

ApprovalChoice = Literal["allow_once", "allow_session", "deny"]


@dataclass(frozen=True)
class ApprovalResult:
    choice: ApprovalChoice
    reason: str = ""


class ApprovalBroker(Protocol):
    async def request_approval(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        reason: str,
        source: str,
        is_interactive: bool,
    ) -> ApprovalResult: ...


class DefaultApprovalBroker:
    """Decline approval requests when the host has no approval UI."""

    async def request_approval(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        reason: str,
        source: str,
        is_interactive: bool,
    ) -> ApprovalResult:
        return ApprovalResult("deny", reason or "No approval handler is configured.")
