"""ASGI request-body ceiling enforced before multipart parsing or route code.

This is the backstop, not the upload limit. It bounds what the process will
buffer for one request — a client that lies about `content-length`, or streams
without end — and answers `request_too_large`. The limit a *user* is subject to
is per file, checked by the routes against `max_upload_bytes`, which answer
`upload_too_large`. The two are deliberately different numbers: see
`MULTIPART_OVERHEAD_BYTES`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from app.core.config import settings


class _BodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # The *request* ceiling, not the per-file cap. A multipart body carrying a
        # file at the cap is larger than the file, so limiting the body to the
        # per-file number rejected legal uploads here and left every route's own
        # `upload_too_large` guard unreachable.
        limit = settings.max_request_bytes
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > limit:
                    await self._reject(send)
                    return
            except ValueError:
                pass

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        async def tracked_send(message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _BodyTooLarge:
            if not response_started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Callable[[dict], Awaitable[None]]) -> None:
        payload = json.dumps({"detail": "request_too_large"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
