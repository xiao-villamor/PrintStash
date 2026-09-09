"""The local migration scheduler honors recovery and contains worker failures."""

import asyncio
import logging
from unittest.mock import Mock

import pytest

from app.runtime import vault_migrations
from app.runtime.maintenance import end_restore_maintenance, hold_restore_maintenance


@pytest.fixture
def scheduler_tick(monkeypatch):
    delays = []

    async def stop_at_next_tick(seconds):
        delays.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(vault_migrations.asyncio, "sleep", stop_at_next_tick)
    return delays


class TestRunMigrations:
    @pytest.mark.asyncio
    async def test_recovery_maintenance_defers_copy_work(
        self, monkeypatch, scheduler_tick
    ):
        work = Mock()
        monkeypatch.setattr(vault_migrations.VaultMigrations, "work_pending", work)
        hold_restore_maintenance()
        try:
            with pytest.raises(asyncio.CancelledError):
                await vault_migrations.run_migrations()
        finally:
            end_restore_maintenance()
        work.assert_not_called()
        assert scheduler_tick == [1]

    @pytest.mark.asyncio
    async def test_worker_failure_is_logged_without_provider_details(
        self, db_session, monkeypatch, scheduler_tick, caplog
    ):
        work = Mock(side_effect=OSError("credential=fake-secret"))
        monkeypatch.setattr(vault_migrations.VaultMigrations, "work_pending", work)
        with caplog.at_level(logging.WARNING, logger=vault_migrations.logger.name):
            with pytest.raises(asyncio.CancelledError):
                await vault_migrations.run_migrations()
        work.assert_called_once_with()
        assert "inspect its safe progress report" in caplog.text
        assert "fake-secret" not in caplog.text
        assert scheduler_tick == [1]
