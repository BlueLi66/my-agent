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
