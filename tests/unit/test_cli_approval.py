from io import StringIO

import pytest

from my_agent.cli.approval import TerminalApprovalBroker
from my_agent.cli.render import ConsoleRenderer
from my_agent.core.events import TextDelta
from my_agent.core.types import ToolCall


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "expected"), [
    ("y", "allow_once"), (" A ", "allow_session"), ("N", "deny"), ("", "deny"),
])
async def test_terminal_approval_choices_close_stream_line_first(answer, expected):
    output = StringIO()
    renderer = ConsoleRenderer(output)
    renderer(TextDelta("session", "partial"))

    def read(prompt):
        assert output.getvalue() == "partial\n"
        assert "[permission] shell" in prompt
        assert "[y]" in prompt and "[a]" in prompt and "[N]" in prompt
        return answer

    broker = TerminalApprovalBroker(renderer, read=read)
    result = await broker.request_approval(
        session_id="session", tool_call=ToolCall("id", "shell", {}), reason="test approval",
        source="permission", is_interactive=True,
    )
    assert result.choice == expected


@pytest.mark.asyncio
async def test_terminal_broker_can_decline_without_reading_input():
    def read(prompt):
        raise AssertionError("Async runs must not prompt")

    broker = TerminalApprovalBroker(ConsoleRenderer(StringIO()), read=read)
    result = await broker.request_approval(
        session_id="session", tool_call=ToolCall("id", "shell", {}), reason="test approval",
        source="permission", is_interactive=False,
    )
    assert result.choice == "deny"
