import pytest

from my_agent.core.approval import ApprovalResult
from my_agent.core.hooks import Ask, Deny, Extension
from my_agent.core.types import ToolCall


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["allow_once", "allow_session", "deny"])
async def test_approval_result_reaches_bound_extensions_in_registration_order(make_context, choice):
    seen = []

    class Observer(Extension):
        def __init__(self, name, asks=False):
            self.name = name
            self.asks = asks

        async def on_session_start(self, ctx):
            seen.append(self.name)

        async def before_tool(self, ctx, call):
            if self.asks:
                return Ask("confirm test tool")

        async def on_tool_approved(self, ctx, call, result):
            seen.append((self.name, call.id, result))

    approval = ApprovalResult(choice, "user decision")

    class Broker:
        async def request_approval(self, **request):
            assert request["tool_call"] is call
            assert request["source"] == "first"
            assert request["reason"] == "confirm test tool"
            return approval

    call = ToolCall("id", "test", {})
    ctx = make_context(extensions=[Observer("first", True), Observer("second")])
    ctx.approval = Broker()
    await ctx.hooks.on_session_start(ctx)
    decision = await ctx.hooks.before_tool(ctx, call)

    if choice == "deny":
        assert decision == Deny("user decision")
        assert seen == ["first", "second"]
    else:
        assert decision is approval
        assert seen == ["first", "second", ("first", "id", approval), ("second", "id", approval)]
