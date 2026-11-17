import argparse
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from anthropic import AsyncAnthropic

from my_agent.config import ConfigError, load_config
from my_agent.core.approval import DefaultApprovalBroker
from my_agent.core.cancel import CancelToken
from my_agent.core.context import RunContext
from my_agent.core.errors import ModelError
from my_agent.core.events import EventBus
from my_agent.core.hooks import HookDispatcher
from my_agent.core.loop import submit
from my_agent.core.session import Session
from my_agent.persistence.store import InMemorySessionStore
from my_agent.provider.anthropic import AnthropicProvider
from my_agent.tools.builtin import builtin_tools
from my_agent.tools.registry import ToolRegistry

from .render import ConsoleRenderer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="my-agent", description="Run the interactive coding agent.")
    parser.add_argument("-C", "--workdir", type=Path, default=Path.cwd(), help="Workspace directory")
    parser.add_argument("--env-file", type=Path, help="Additional .env file")
    args = parser.parse_args(argv)
    workdir = args.workdir.resolve()
    if not workdir.is_dir():
        parser.error(f"Workspace does not exist: {workdir}")
    try:
        config = load_config(workdir, env_file=args.env_file)
    except ConfigError as error:
        parser.error(str(error))

    client = AsyncAnthropic(api_key=config.api_key, base_url=config.base_url, max_retries=0)
    shell = "cmd.exe" if os.name == "nt" else "sh"
    events = EventBus()
    events.subscribe(ConsoleRenderer(sys.stdout))
    ctx = RunContext(
        session=Session(uuid4().hex),
        provider=AnthropicProvider(client, config.model_id, fallback_model=config.fallback_model_id),
        tools=ToolRegistry(builtin_tools()),
        hooks=HookDispatcher(),
        events=events,
        approval=DefaultApprovalBroker(),
        store=InMemorySessionStore(),
        cancel=CancelToken(),
        workdir=workdir,
        system_prompt=(
            f"You are a coding agent working in {workdir}. Use the file tools for workspace edits. "
            f"The bash tool uses {shell}; use that shell's syntax."
        ),
    )

    with asyncio.Runner() as runner:
        try:
            while True:
                text = input("my-agent> ").strip()
                if text in ("q", "exit"):
                    return 0
                if not text:
                    continue
                ctx = replace(ctx, cancel=CancelToken())
                outcome = runner.run(submit(ctx, text))
                if outcome.direct_reply is not None:
                    print(outcome.direct_reply)
                if ctx.session.status == "max_turns":
                    print("Run limit reached.", file=sys.stderr)
        except EOFError:
            return 0
        except KeyboardInterrupt:
            print("\nCancelled.", file=sys.stderr)
            return 130
        except ModelError as error:
            print(str(error), file=sys.stderr)
            return 1
        finally:
            runner.run(client.close())
