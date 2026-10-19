"""Native async operations retain synchronous bytes, cleanup and cancellation."""

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from app.modules.storage import opendal_async_reads as bridge


@pytest.fixture
def owned_loop(monkeypatch):
    monkeypatch.setattr(bridge, "_loop", None)
    monkeypatch.setattr(bridge, "_thread", None)
    monkeypatch.setattr(bridge, "_lock", threading.Lock())
    yield
    bridge._shutdown()


class TestNativeReadBridge:
    def test_preserves_native_read_contracts(self, owned_loop):
        native = AsyncMock()
        native.exists.return_value = True
        native.read.return_value = bytearray(b"source")
        native.stat.return_value = {"size": 6}
        native.check.return_value = None
        operator = bridge.AsyncReadOperator(native)
        assert operator.exists("source") is True
        assert operator.read("source") == b"source"
        assert operator.stat("source") == {"size": 6}
        assert operator.check() is None
        assert bridge._get_loop() is bridge._get_loop()

    def test_exhausts_native_listing(self, owned_loop):
        async def entries():
            yield "first"
            yield "second"

        native = AsyncMock()
        native.list.return_value = entries()
        listing = bridge.AsyncReadOperator(native).list("prefix")
        assert list(listing) == ["first", "second"]
        assert list(listing) == []

    def test_closes_reader_after_caller_failure(self, owned_loop):
        native, reader = AsyncMock(), AsyncMock()
        native.open.return_value = reader
        reader.read.return_value = bytearray(b"part")
        with pytest.raises(ValueError, match="caller failed"):
            with bridge.AsyncReadOperator(native).open("source", "rb") as stream:
                assert stream.read(4) == b"part"
                raise ValueError("caller failed")
        reader.close.assert_awaited_once()
        with pytest.raises(ValueError, match="only supports rb"):
            with bridge.AsyncReadOperator(native).open("source", "wb"):
                pytest.fail("opened writer")

    def test_cancels_expired_native_operation(self, owned_loop, monkeypatch):
        cancelled = threading.Event()

        async def waiting():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(bridge, "operation_timeout", lambda: 0.02)
        with pytest.raises(TimeoutError, match="remote native I/O timeout"):
            bridge._run(waiting)
        assert cancelled.wait(2)

    def test_preserves_native_timeout_identity(self, owned_loop):
        async def failed():
            raise TimeoutError("provider timeout")

        with pytest.raises(TimeoutError, match="provider timeout"):
            bridge._run(failed)

    def test_shutdown_cancels_pending_native_tasks(self, owned_loop):
        cancelled = threading.Event()
        started = threading.Event()

        async def pending():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        loop = bridge._get_loop()
        asyncio.run_coroutine_threadsafe(pending(), loop)
        assert started.wait(2)
        bridge._shutdown()
        assert cancelled.is_set()
        assert loop.is_closed()

    def test_fork_discards_inherited_thread_state(self, owned_loop, monkeypatch):
        monkeypatch.setattr(bridge, "_thread", object())
        monkeypatch.setattr(bridge, "_loop", object())
        previous = bridge._lock
        bridge._after_fork()
        assert bridge._lock is not previous
        assert bridge._loop is bridge._thread is None
