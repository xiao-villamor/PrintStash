"""Observable event-loop progress while synchronous request I/O is blocked."""

import asyncio
import threading

import pytest_asyncio


@pytest_asyncio.fixture
async def loop_handshake():
    loop = asyncio.get_running_loop()
    progressed = threading.Event()
    observations: list[bool] = []

    def wait_for_loop() -> None:
        progressed.clear()
        loop.call_soon_threadsafe(progressed.set)
        observations.append(progressed.wait(timeout=1.0))

    return wait_for_loop, observations
