import pytest

from my_agent.core.context import validate_tool_pairs
from my_agent.core.events import TextDelta
from my_agent.core.loop import submit
from my_agent.core.types import ToolResult
from my_agent.provider.anthropic import AnthropicProvider
from my_agent.tools.builtin import builtin_tools


@pytest.mark.asyncio
async def test_streamed_tool_calls_read_edit_and_read_back(make_context, anthropic_client, api_reply):
    client = anthropic_client(
        (["Reading", " source"], api_reply("Reading source", calls=[("r1", "read_file", {"path": "add.py"})])),
        (["Editing"], api_reply("Editing", calls=[("e1", "edit_file", {
            "path": "add.py", "old_text": "a - b", "new_text": "a + b",
        })])),
        ([], api_reply(calls=[("r2", "read_file", {"path": "add.py"})])),
        (["Fix", " complete"], api_reply("Fix complete")),
    )
    ctx = make_context(tools=builtin_tools())
    ctx.provider = AnthropicProvider(client, "mock-model")
    source = ctx.workdir / "add.py"
    source.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    observed = []
    ctx.events.subscribe(observed.append)

    await submit(ctx, "Fix the addition function")

    assert source.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert [event.delta for event in observed if isinstance(event, TextDelta)] == [
        "Reading", " source", "Editing", "Fix", " complete",
    ]
    assert ctx.session.messages[1].blocks[0].text == "Reading source"
    assert ctx.session.messages[-1].blocks[0].text == "Fix complete"
    assert ctx.session.status == "done"
    assert ctx.store.checkpoints[-1].status == "done"
    assert validate_tool_pairs(ctx.session.messages) == []
    old_result = client.requests[1]["messages"][-1]["content"][0]
    assert old_result["type"] == "tool_result" and "return a - b" in old_result["content"]
    new_result = client.requests[3]["messages"][-1]["content"][0]
    assert new_result["tool_use_id"] == "r2" and "return a + b" in new_result["content"]
    assert ctx.session.messages[-2].blocks == [ToolResult("r2", "def add(a, b):\n    return a + b")]
