from collections import deque
from collections.abc import Iterable
from copy import deepcopy

from my_agent.core.cancel import CancelToken
from my_agent.core.context import ModelRequest
from my_agent.core.events import EventBus
from my_agent.core.types import ModelResponse


class FakeProvider:
    """Return scripted responses or raise scripted errors in request order."""

    def __init__(self, responses: Iterable[ModelResponse | Exception]) -> None:
        self._responses = deque(responses)
        self.requests: list[ModelRequest] = []

    async def complete(
        self, request: ModelRequest, cancel: CancelToken, events: EventBus
    ) -> ModelResponse:
        cancel.raise_if_cancelled()
        self.requests.append(deepcopy(request))
        if not self._responses:
            raise AssertionError("FakeProvider received more requests than scripted")

        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)
