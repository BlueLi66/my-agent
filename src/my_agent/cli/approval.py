from collections.abc import Callable

from my_agent.core.approval import ApprovalResult
from my_agent.core.errors import Cancelled
from my_agent.core.types import ToolCall

from .render import ConsoleRenderer


class TerminalApprovalBroker:
    def __init__(self, renderer: ConsoleRenderer, *, read: Callable[[str], str] | None = None) -> None:
        self._renderer = renderer
        self._read = read if read is not None else input

    async def request_approval(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        reason: str,
        source: str,
        is_interactive: bool,
    ) -> ApprovalResult:
        if not is_interactive:
            return ApprovalResult("deny", "Tool approval requires an interactive run.")
        self._renderer.end_line()
        prompt = f"[{source}] {tool_call.name}\n{reason}\nAllow? [y] once / [a] this session / [N] deny "
        try:
            choice = self._read(prompt).strip().casefold()
        except EOFError:
            return ApprovalResult("deny", "Approval input was closed.")
        except KeyboardInterrupt:
            raise Cancelled("Approval interrupted") from None
        if choice in ("y", "yes"):
            return ApprovalResult("allow_once")
        if choice == "a":
            return ApprovalResult("allow_session")
        return ApprovalResult("deny", "Approval declined.")
