"""Synchronous read contracts backed by OpenDAL's non-blocking native API.

The blocking Python binding retains the GIL across network I/O. One lazy loop
per process drives native futures instead; callers wait with the GIL released.
Existing worker admission still bounds concurrent requests and stream buffers.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from app.modules.storage.remote_deadline import operation_timeout

_Result = TypeVar("_Result")
_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None


def _after_fork() -> None:
    global _lock, _loop, _thread
    _lock = threading.Lock()
    _loop = None
    _thread = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            _thread = threading.Thread(
                target=_run_loop, args=(_loop,), name="remote-native-io", daemon=True
            )
            _thread.start()
        return _loop


@atexit.register
def _shutdown() -> None:
    if _loop is not None and not _loop.is_closed():
        _loop.call_soon_threadsafe(_loop.stop)
    if _thread is not None:
        _thread.join(timeout=5)


def _run(
    operation: Callable[[], Awaitable[_Result]], *, cleanup: bool = False
) -> _Result:
    # Construct native futures on a running event loop. run_coroutine_threadsafe
    # copies the caller context, including its original deadline/cancellation.
    timeout = 5.0 if cleanup else operation_timeout()
    end = time.monotonic() + timeout

    async def invoke() -> _Result:
        return await operation()

    future = asyncio.run_coroutine_threadsafe(invoke(), _get_loop())
    try:
        while True:
            if not cleanup:
                operation_timeout()
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("remote native I/O timeout")
            try:
                return future.result(timeout=min(0.05, remaining))
            except TimeoutError:
                if future.done():
                    return future.result()
    finally:
        if not future.done():
            future.cancel()


class _Reader:
    def __init__(self, native: Any) -> None:
        self._native = native

    def read(self, size: int) -> bytes:
        return bytes(_run(lambda: self._native.read(size)))


class _Listing(Iterator[Any]):
    def __init__(self, native: Any) -> None:
        self._native = native

    def __next__(self) -> Any:
        native = self._native
        if native is None:
            raise StopIteration
        try:
            return _run(lambda: anext(native))
        except StopAsyncIteration:
            self.close()
            raise StopIteration from None

    def close(self) -> None:
        # Native AsyncLister has no close method. Dropping it releases its
        # pagination state; there is no background prefetch or Python queue.
        self._native = None


class AsyncReadOperator:
    """Bounded read-only bridge; managed publication keeps its own contract."""

    def __init__(self, native: Any) -> None:
        self._native = native

    def exists(self, key: str) -> bool:
        return bool(_run(lambda: self._native.exists(key)))

    def stat(self, key: str) -> Any:
        return _run(lambda: self._native.stat(key))

    def read(self, key: str) -> bytes:
        return bytes(_run(lambda: self._native.read(key)))

    def check(self) -> None:
        _run(self._native.check)

    def list(self, directory: str) -> _Listing:
        return _Listing(_run(lambda: self._native.list(directory)))

    @contextmanager
    def open(self, key: str, mode: str, **options: Any) -> Iterator[_Reader]:
        if mode != "rb":
            raise ValueError("native read bridge only supports rb")
        native = _run(lambda: self._native.open(key, mode, **options))
        try:
            yield _Reader(native)
        finally:
            # Cleanup must still run after the caller is cancelled or expires.
            # Closing a reader has no remote write/publication side effects.
            _run(native.close, cleanup=True)
