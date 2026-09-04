"""The automatic backup claim is durable across scheduler ticks and failures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from app.db.models import SystemConfig
from app.services import backup_schedule
from app.services.backup_destination import BackupTrigger


class TestClaimDueBackup:
    def test_claims_a_due_day_once(
        self, db_session: Session, make_system_config
    ) -> None:
        make_system_config(
            automatic_backups_enabled=True,
            automatic_backup_time_utc="02:00",
        )
        now = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)

        first = backup_schedule.claim_due_backup(db_session, now=now)
        second = backup_schedule.claim_due_backup(db_session, now=now)

        assert first is True
        assert second is False
        stored = db_session.get(SystemConfig, 1)
        assert stored is not None
        assert stored.automatic_backup_last_attempt_at == now.replace(tzinfo=None)


class TestRunDueBackup:
    def test_creates_with_the_automatic_destination_policy(
        self,
        make_system_config,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        make_system_config(
            automatic_backups_enabled=True,
            automatic_backup_time_utc="02:00",
        )
        now = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
        triggers: list[BackupTrigger] = []

        def create_backup(*, trigger: BackupTrigger) -> None:
            triggers.append(trigger)

        monkeypatch.setattr(backup_schedule.backup, "create_backup", create_backup)

        assert backup_schedule.run_due_backup(now=now) is True
        assert triggers == [BackupTrigger.AUTOMATIC]

    def test_records_a_failed_attempt(
        self,
        db_session: Session,
        make_system_config,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        make_system_config(
            automatic_backups_enabled=True,
            automatic_backup_time_utc="02:00",
        )
        now = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)

        def fail_backup(**_kwargs: object) -> None:
            raise RuntimeError("archive failed")

        monkeypatch.setattr(backup_schedule.backup, "create_backup", fail_backup)

        with pytest.raises(RuntimeError, match="archive failed"):
            backup_schedule.run_due_backup(now=now)

        db_session.expire_all()
        stored = db_session.get(SystemConfig, 1)
        assert stored is not None
        assert stored.automatic_backup_last_attempt_at == now.replace(tzinfo=None)
