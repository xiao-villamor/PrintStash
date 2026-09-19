"""One private inference HTTP pool, independent of ASGI and ordinary egress.

Synchronous provider consumers share a small async transport loop. Cancellation
interrupts a socket read, so a slow/trickling endpoint cannot reset a deadline.
No request payload or response ever goes through a file or process argument.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import httpx
from printstash_core.inference.context import InferenceContext

_lock = threading.Lock()
_runtime: Runtime | None = None
_private_wire: ContextVar[bool] = ContextVar("private_inference_wire", default=False)


class _WirePrivacy(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX's URL and httpcore's DEBUG response headers may carry secrets.
        # Only inference's private thread is suppressed; ordinary egress keeps
        # its configured diagnostics. Names are the pinned httpx/httpcore owners.
        return record.threadName != "inference-http" and not _private_wire.get()


_privacy = _WirePrivacy()


@contextmanager
def private_http():
    """Model download redirects may contain signed credentials too."""
    for name in (
        "httpx",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    ):
        logging.getLogger(name).addFilter(_privacy)
    token = _private_wire.set(True)
    try:
        yield
    finally:
        _private_wire.reset(token)


@dataclass
class Runtime:
    loop: asyncio.AbstractEventLoop
    thread: threading.Thread
    client: httpx.AsyncClient | None = None


def _get_runtime() -> Runtime:
    global _runtime
    with _lock:
        if _runtime is None:
            for name in (
                "httpx",
                "httpcore.connection",
                "httpcore.http11",
                "httpcore.http2",
                "httpcore.proxy",
                "httpcore.socks",
            ):
                logging.getLogger(name).addFilter(_privacy)
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="inference-http", daemon=True
            )
            _runtime = Runtime(loop, thread)
            thread.start()
        return _runtime


async def _request(
    runtime: Runtime,
    url: str,
    content: bytes,
    headers: dict[str, str],
    seconds: float,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    # Construct and use the pool only on its owning loop.
    if runtime.client is None:
        runtime.client = httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )
    async with asyncio.timeout(seconds):
        timeout = httpx.Timeout(
            min(5, seconds), connect=min(2, seconds), pool=min(1, seconds)
        )
        async with runtime.client.stream(
            "POST", url, content=content, headers=headers, timeout=timeout
        ) as response:
            response_headers = dict(response.headers)
            if 300 <= response.status_code < 400:
                return response.status_code, response_headers, b""
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise ValueError("inference_response_encoding")
            raw = bytearray()
            async for chunk in response.aiter_raw():
                if len(raw) + len(chunk) > max_bytes:
                    raise ValueError("inference_response_too_large")
                raw.extend(chunk)
            return response.status_code, response_headers, bytes(raw)


def request(
    url: str,
    content: bytes,
    headers: dict[str, str],
    context: InferenceContext,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    seconds = context.remaining()
    runtime = _get_runtime()
    future = asyncio.run_coroutine_threadsafe(
        _request(runtime, url, content, headers, seconds, max_bytes), runtime.loop
    )
    try:
        while True:
            context.remaining()
            try:
                return future.result(timeout=min(0.05, context.remaining()))
            except FutureTimeout:
                if future.done():
                    raise
    finally:
        if not future.done():
            future.cancel()


def close() -> None:
    global _runtime
    with _lock:
        runtime, _runtime = _runtime, None
        if runtime is None:
            return

        async def shutdown() -> None:
            pending = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            ]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if runtime.client is not None:
                await runtime.client.aclose()

        try:
            asyncio.run_coroutine_threadsafe(shutdown(), runtime.loop).result(timeout=5)
        finally:
            runtime.loop.call_soon_threadsafe(runtime.loop.stop)
            runtime.thread.join(timeout=5)
            if not runtime.thread.is_alive():
                runtime.loop.close()
