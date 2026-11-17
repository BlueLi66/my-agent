from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from my_agent.core.approval import DefaultApprovalBroker
from my_agent.core.cancel import CancelToken
from my_agent.core.context import RunContext
from my_agent.core.events import EventBus
from my_agent.core.hooks import HookDispatcher
from my_agent.core.session import Session
from my_agent.persistence.store import InMemorySessionStore
from my_agent.provider.fake import FakeProvider
from my_agent.tools.registry import ToolRegistry


@pytest.fixture
def make_context(tmp_path):
    def create(responses=(), *, tools=(), extensions=()):
        return RunContext(
            session=Session("test-session"),
            provider=FakeProvider(responses),
            tools=ToolRegistry(tools),
            hooks=HookDispatcher(extensions),
            events=EventBus(),
            approval=DefaultApprovalBroker(),
            store=InMemorySessionStore(),
            cancel=CancelToken(),
            workdir=tmp_path,
            system_prompt="Test system prompt",
        )

    return create


@pytest.fixture
def anthropic_client():
    def create(*script):
        client = SimpleNamespace(requests=[], closed_streams=0, closed=False)
        responses = iter(script)

        @asynccontextmanager
        async def stream(**params):
            client.requests.append(deepcopy(params))
            reply = next(responses)
            if isinstance(reply, Exception):
                raise reply
            deltas, final_message = reply

            async def text_stream():
                if callable(deltas):
                    async for delta in deltas():
                        yield delta
                    return
                for delta in deltas:
                    if isinstance(delta, Exception):
                        raise delta
                    yield delta

            async def get_final_message():
                return final_message

            try:
                yield SimpleNamespace(text_stream=text_stream(), get_final_message=get_final_message)
            finally:
                client.closed_streams += 1

        async def close():
            client.closed = True

        client.close = close
        client.messages = SimpleNamespace(stream=stream)
        return client

    return create


@pytest.fixture
def api_reply():
    def create(text="", *, calls=(), reason=None):
        content = [SimpleNamespace(type="text", text=text)] if text else []
        content.extend(
            SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)
            for call_id, name, args in calls
        )
        return SimpleNamespace(
            content=content,
            stop_reason=reason or ("tool_use" if calls else "end_turn"),
            usage=SimpleNamespace(
                input_tokens=8, output_tokens=2, cache_read_input_tokens=1, cache_creation_input_tokens=3,
            ),
        )

    return create
