"""ASGI request boundary for matching catalog rows and storage adapters."""

from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.modules.storage.storage_backend import generations
from app.runtime.maintenance import (
    begin_mutating_operation,
    end_mutating_operation,
    restore_in_progress,
)


class VaultGenerationMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "").rstrip("/")
        if path == "/api/v1/storage/migrations" or path.startswith(
            "/api/v1/storage/migrations/"
        ):
            if (
                restore_in_progress()
                and scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
                and not path.endswith("/recover")
            ):
                await JSONResponse(
                    {"detail": "restore_in_progress"},
                    status_code=503,
                    headers={"Retry-After": "5"},
                )(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        is_restore = path.startswith("/api/v1/backups/") and path.endswith("/restore")
        is_recovery_login = path == "/api/v1/auth/login" and restore_in_progress()
        if is_recovery_login:
            scope.setdefault("state", {})["restore_recovery_login"] = True
        mutating = (
            scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
            and not is_restore
            and not is_recovery_login
        )
        if mutating and not begin_mutating_operation():
            await JSONResponse(
                {"detail": "restore_in_progress"},
                status_code=503,
                headers={"Retry-After": "5"},
            )(scope, receive, send)
            return
        try:
            await self._pinned_request(scope, receive, send)
        finally:
            if mutating:
                end_mutating_operation()

    async def _pinned_request(self, scope: Scope, receive: Receive, send: Send) -> None:
        admission = asyncio.create_task(asyncio.to_thread(generations.pin))
        try:
            pinned = await asyncio.shield(admission)
        except asyncio.CancelledError:

            def release_admission(task):
                if not task.cancelled() and task.exception() is None:
                    task.result().close()

            admission.add_done_callback(release_admission)
            raise
        except RuntimeError as exc:
            if "storage_backend_not_bound" not in str(exc):
                raise
            await self.app(scope, receive, send)
            return
        with generations.use(pinned):

            async def planned_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    pinned.planned()
                await send(message)

            await self.app(scope, receive, planned_send)
