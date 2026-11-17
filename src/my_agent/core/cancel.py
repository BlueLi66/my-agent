import asyncio
import logging
from collections.abc import Awaitable, Callable
from weakref import WeakSet

from .errors import Cancelled


class CancelToken:
    def __init__(self) -> None:
        self._reason: str | None = None
        self._parent: CancelToken | None = None
        self._children: WeakSet[CancelToken] = WeakSet()
        self._callbacks: dict[object, Callable[[], None]] = {}

    @property
    def is_cancelled(self) -> bool:
        return self._reason is not None

    def raise_if_cancelled(self) -> None:
        if self._reason is not None:
            raise Cancelled(self._reason)

    async def run[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        """Cancel an awaited operation and let its cleanup complete."""
        self.raise_if_cancelled()
        task = asyncio.ensure_future(operation())
        unsubscribe = self.on_cancel(task.cancel)
        try:
            result = await task
            self.raise_if_cancelled()
            return result
        except asyncio.CancelledError:
            if not asyncio.current_task().cancelling():
                self.raise_if_cancelled()
            raise
        finally:
            unsubscribe()

    def child(self) -> "CancelToken":
        child = CancelToken()
        if self._reason is not None:
            child.cancel(self._reason)
        else:
            child._parent = self
            self._children.add(child)
        return child

    def cancel(self, reason: str = "cancelled") -> None:
        if self.is_cancelled:
            return
        self._reason = reason
        if self._parent is not None:
            self._parent._children.discard(self)
            self._parent = None

        children = list(self._children)
        self._children.clear()
        for child in children:
            child.cancel(reason)

        callbacks = tuple(self._callbacks.values())
        self._callbacks.clear()
        for callback in callbacks:
            self._invoke(callback)

    def on_cancel(self, cb: Callable[[], None]) -> Callable[[], None]:
        key = object()
        if self.is_cancelled:
            self._invoke(cb)
        else:
            self._callbacks[key] = cb

        def unsubscribe() -> None:
            self._callbacks.pop(key, None)

        return unsubscribe

    @staticmethod
    def _invoke(callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            logging.getLogger(__name__).exception("Cancellation cleanup failed")
