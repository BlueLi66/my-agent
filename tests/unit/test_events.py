import asyncio

import pytest

from my_agent.core.events import EventBus, TextDelta


@pytest.mark.asyncio
async def test_text_deltas_await_subscribers_in_order_and_survive_errors(caplog):
    bus = EventBus()
    received = []

    async def first(event):
        received.append(("first started", event.delta))
        await asyncio.sleep(0)
        received.append(("first finished", event.delta))

    async def broken(event):
        await asyncio.sleep(0)
        raise RuntimeError("subscriber broke")

    def last(event):
        received.append(("last", event.delta))

    remove = bus.subscribe(first)
    bus.subscribe(broken)
    bus.subscribe(last)

    await bus.emit(TextDelta("session", "a"))
    await bus.emit(TextDelta("session", "b"))
    remove()
    remove()
    await bus.emit(TextDelta("session", "c"))

    assert received == [
        ("first started", "a"), ("first finished", "a"), ("last", "a"),
        ("first started", "b"), ("first finished", "b"), ("last", "b"),
        ("last", "c"),
    ]
    assert caplog.text.count("subscriber broke") >= 3
