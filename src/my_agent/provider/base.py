from typing import Protocol

from my_agent.core.cancel import CancelToken
from my_agent.core.context import ModelRequest
from my_agent.core.events import EventBus
from my_agent.core.types import ModelResponse


class Provider(Protocol):
    async def complete(
        self, request: ModelRequest, cancel: CancelToken, events: EventBus
    ) -> ModelResponse:
        """Send the request with injections after its history and honor cancellation."""
        ...
