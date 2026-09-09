"""Request admission preserves maintenance and releases cancelled read generations."""

import asyncio
import json
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.vault_generation import VaultGenerationMiddleware
from app.modules.storage.storage_backend import generations
from app.runtime.maintenance import end_restore_maintenance, hold_restore_maintenance


class TestVaultGenerationMiddleware:
    @pytest.mark.asyncio
    async def test_migration_mutations_are_refused_during_recovery(self):
        application, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
        hold_restore_maintenance()
        try:
            await VaultGenerationMiddleware(application)(
                {
                    "type": "http",
                    "path": "/api/v1/storage/migrations/run/pause",
                    "method": "POST",
                },
                receive,
                send,
            )
        finally:
            end_restore_maintenance()
        application.assert_not_awaited()
        headers, body = [call.args[0] for call in send.await_args_list]
        assert headers["status"] == 503
        assert (b"retry-after", b"5") in headers["headers"]
        assert json.loads(body["body"]) == {"detail": "restore_in_progress"}

    @pytest.mark.asyncio
    async def test_cancelled_admission_releases_its_late_generation(
        self, db_session, monkeypatch
    ):
        entered, release, closed = Event(), Event(), Event()
        real_pin, real_close = generations.pin, generations.ReadGeneration.close
        epoch = generations.current_epoch()
        application = AsyncMock()

        def delayed_pin():
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test admission was not released")
            return real_pin()

        def observed_close(lease):
            real_close(lease)
            closed.set()

        monkeypatch.setattr(generations, "pin", delayed_pin)
        monkeypatch.setattr(generations.ReadGeneration, "close", observed_close)
        request = asyncio.create_task(
            VaultGenerationMiddleware(application)(
                {"type": "http", "path": "/api/v1/files/1/download", "method": "GET"},
                AsyncMock(),
                AsyncMock(),
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            release.set()
            assert await asyncio.to_thread(closed.wait, 2)
            assert not generations.has_readers(epoch)
            application.assert_not_awaited()
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_unexpected_admission_failure_is_not_swallowed(self, monkeypatch):
        application = AsyncMock()
        monkeypatch.setattr(
            generations, "pin", Mock(side_effect=RuntimeError("broken admission"))
        )
        with pytest.raises(RuntimeError, match="broken admission"):
            await VaultGenerationMiddleware(application)(
                {"type": "http", "path": "/api/v1/files/1/download", "method": "GET"},
                AsyncMock(),
                AsyncMock(),
            )
        application.assert_not_awaited()
