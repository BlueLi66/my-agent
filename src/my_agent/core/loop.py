import asyncio
from copy import deepcopy
from dataclasses import dataclass

from my_agent.tools.base import ToolEnv

from .context import RunContext, build_request
from .errors import Cancelled, ModelError
from .events import ModelFinished, ModelStarted, SessionEnded, SessionStarted, ToolFinished, ToolStarted
from .hooks import Deny, InputHandled
from .session import Session
from .types import Message, TextBlock, ToolCall, ToolResult

CONTINUATION_PROMPT = "Continue where your previous response was cut off."


@dataclass(frozen=True)
class SubmitOutcome:
    session: Session
    direct_reply: str | None = None


async def submit(ctx: RunContext, user_text: str) -> SubmitOutcome:
    action = await ctx.hooks.on_user_input(ctx, user_text)
    if isinstance(action, InputHandled):
        return SubmitOutcome(session=ctx.session, direct_reply=action.reply)

    ctx.session.messages.append(Message("user", [TextBlock(action.text)]))
    return SubmitOutcome(session=await run(ctx))


def _limit_reached(ctx: RunContext, run_turns: int) -> bool:
    if run_turns >= ctx.limits.max_turns:
        return True

    budget = ctx.limits.max_tokens_budget
    usage = ctx.session.usage
    total_tokens = (
        usage.input_tokens + usage.output_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    )
    return budget is not None and total_tokens >= budget


async def _run_one_tool(ctx: RunContext, call: ToolCall) -> ToolResult:
    ctx.cancel.raise_if_cancelled()
    await ctx.events.emit(ToolStarted(ctx.session.id, call.id, call.name))
    ctx.cancel.raise_if_cancelled()
    decision = await ctx.hooks.before_tool(ctx, call)
    ctx.cancel.raise_if_cancelled()

    if isinstance(decision, Deny):
        result = ToolResult(call.id, decision.reason, is_error=True)
    else:
        env = ToolEnv(ctx.workdir, ctx.session.id, ctx.session.ext, ctx.cancel, ctx.is_interactive)
        result = await ctx.tools.execute(call, env)
        result = await ctx.hooks.after_tool(ctx, call, result)

    await ctx.events.emit(ToolFinished(ctx.session.id, result))
    ctx.cancel.raise_if_cancelled()
    return result


async def run(ctx: RunContext) -> Session:
    session = ctx.session
    session.status = "running"
    run_turns = 0
    max_tokens_override: int | None = None
    continuations = 0
    retried_model_error = False

    try:
        await ctx.events.emit(SessionStarted(session.id))
        await ctx.hooks.on_session_start(ctx)
        while True:
            ctx.cancel.raise_if_cancelled()
            if _limit_reached(ctx, run_turns):
                session.status = "max_turns"
                await ctx.store.save_status(session)
                break

            run_turns += 1
            request = build_request(ctx, max_tokens_override)
            await ctx.hooks.before_model(ctx, request)
            ctx.cancel.raise_if_cancelled()
            await ctx.events.emit(ModelStarted(session.id, session.turn + 1))
            ctx.cancel.raise_if_cancelled()
            try:
                response = await ctx.provider.complete(request, ctx.cancel, ctx.events)
            except ModelError as error:
                ctx.cancel.raise_if_cancelled()
                recovered = await ctx.hooks.on_model_error(ctx, error, request)
                ctx.cancel.raise_if_cancelled()
                if recovered and not retried_model_error:
                    retried_model_error = True
                    continue
                raise

            session.usage += response.usage
            await ctx.events.emit(ModelFinished(session.id, session.turn + 1, response.stop_reason))
            ctx.cancel.raise_if_cancelled()
            retried_model_error = False

            truncated = response.stop_reason == "max_tokens"
            if truncated and max_tokens_override is None:
                max_tokens_override = request.max_tokens * 2
                continue

            max_tokens_override = None
            tool_calls = response.tool_calls
            session.messages.append(deepcopy(response.message))
            await ctx.hooks.after_model(ctx, response)
            ctx.cancel.raise_if_cancelled()

            if tool_calls:
                results = [await _run_one_tool(ctx, call) for call in tool_calls]
                session.messages.append(Message("tool", results))
                continuations = 0
            elif truncated and continuations < 2:
                continuations += 1
                session.messages.append(Message("user", [TextBlock(CONTINUATION_PROMPT)]))
            else:
                continuations = 0
                stop = await ctx.hooks.before_stop(ctx, response)
                ctx.cancel.raise_if_cancelled()
                if stop.action == "continue":
                    session.messages.append(Message("user", [TextBlock(stop.message or "")]))
                else:
                    session.status = "deferred" if stop.action == "defer" else "done"

            session.turn += 1
            await ctx.store.checkpoint(session)
            if session.status != "running":
                break
    except Cancelled:
        session.status = "cancelled"
        await ctx.store.save_status(session)
    except asyncio.CancelledError:
        ctx.cancel.cancel()
        session.status = "cancelled"
        await ctx.store.save_status(session)
        raise
    except Exception:
        session.status = "failed"
        await ctx.store.save_status(session)
        raise
    finally:
        await ctx.hooks.on_session_end(ctx)
        await ctx.events.emit(SessionEnded(session.id, session.status))

    return session
