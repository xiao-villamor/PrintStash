"""Defends ``test_invalid_content_length_is_left_to_the_application`` behavior for the ``core`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.core.body_limit import RequestBodyLimitMiddleware
from app.core.config import settings

Message = dict[str, Any]


def _run_asgi(
    app: Callable[
        [
            Message,
            Callable[[], Awaitable[Message]],
            Callable[[Message], Awaitable[None]],
        ],
        Awaitable[None],
    ],
    *,
    headers: list[tuple[bytes, bytes]],
    body: bytes = b"",
) -> list[Message]:
    sent: list[Message] = []
    received = False

    async def receive() -> Message:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Message = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    asyncio.run(RequestBodyLimitMiddleware(app)(scope, receive, send))
    return sent


def test_invalid_content_length_is_left_to_the_application() -> None:
    async def app(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = _run_asgi(app, headers=[(b"content-length", b"not-a-number")])

    assert sent[0]["status"] == 204


def test_streamed_body_over_limit_is_rejected_before_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(type(settings), "max_upload_bytes", property(lambda _self: 3))

    async def app(_scope, receive, _send) -> None:
        await receive()

    sent = _run_asgi(app, headers=[], body=b"four")

    assert sent[0]["status"] == 413
    assert sent[1]["body"] == b'{"detail": "request_too_large"}'


def test_streamed_body_at_limit_reaches_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(type(settings), "max_upload_bytes", property(lambda _self: 3))

    async def app(_scope, receive, send) -> None:
        message = await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(message["body"])).encode())],
            }
        )
        await send({"type": "http.response.body", "body": message["body"]})

    sent = _run_asgi(app, headers=[], body=b"123")

    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"123"
