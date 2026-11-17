import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .session import SessionStatus
from .types import StopReason, ToolResult


@dataclass(frozen=True)
class Event:
    session_id: str


@dataclass(frozen=True)
class SessionStarted(Event):
    pass


@dataclass(frozen=True)
class SessionEnded(Event):
    status: SessionStatus


@dataclass(frozen=True)
class ModelStarted(Event):
    turn: int


@dataclass(frozen=True)
class ModelFinished(Event):
    turn: int
    stop_reason: StopReason


@dataclass(frozen=True)
class ModelRetry(Event):
    delay_s: float
    status_code: int | None = None


@dataclass(frozen=True)
class TextDelta(Event):
    delta: str


@dataclass(frozen=True)
class ToolStarted(Event):
    call_id: str
    name: str


@dataclass(frozen=True)
class ToolFinished(Event):
    result: ToolResult


@dataclass(frozen=True)
class HookError(Event):
    extension: str
    hook: str
    error: str


EventHandler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[object, EventHandler] = {}

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        key = object()
        self._handlers[key] = handler

        def unsubscribe() -> None:
            self._handlers.pop(key, None)

        return unsubscribe

    async def emit(self, event: Event) -> None:
        """Await subscribers in registration order for each emitted event."""
        for handler in tuple(self._handlers.values()):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logging.getLogger(__name__).exception("Event subscriber failed for %s", type(event).__name__)
