"""Tests for services.realtime.InProcessBus fan-out."""

from __future__ import annotations

import asyncio

from app.services.realtime import InProcessBus


class FakeSocket:
    def __init__(self, *, delay: float = 0.0, raises: bool = False):
        self.delay = delay
        self.raises = raises
        self.received: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise RuntimeError("send failed")
        self.received.append(payload)


def test_slow_subscriber_does_not_delay_others():
    async def _run():
        bus = InProcessBus()
        slow = FakeSocket(delay=10.0)
        fast = FakeSocket()
        await bus.subscribe("printer:1", slow)
        await bus.subscribe("printer:1", fast)

        async with asyncio.timeout(3.0):
            await bus.publish("printer:1", {"hello": "world"})

        assert fast.received == [{"hello": "world"}]
        assert slow.received == []

    asyncio.run(_run())


def test_slow_subscriber_is_dropped_after_timeout():
    async def _run():
        bus = InProcessBus()
        slow = FakeSocket(delay=10.0)
        await bus.subscribe("printer:1", slow)

        async with asyncio.timeout(3.0):
            await bus.publish("printer:1", {"hello": "world"})

        assert slow not in bus._subscribers["printer:1"]

    asyncio.run(_run())


def test_dead_subscriber_is_dropped():
    async def _run():
        bus = InProcessBus()
        dead = FakeSocket(raises=True)
        alive = FakeSocket()
        await bus.subscribe("printer:1", dead)
        await bus.subscribe("printer:1", alive)

        await bus.publish("printer:1", {"x": 1})

        assert dead not in bus._subscribers["printer:1"]
        assert alive.received == [{"x": 1}]

    asyncio.run(_run())


def test_publish_to_empty_channel_is_a_noop():
    async def _run():
        bus = InProcessBus()
        await bus.publish("printer:999", {"x": 1})  # must not raise

    asyncio.run(_run())
