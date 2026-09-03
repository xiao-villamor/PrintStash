"""Automatic backup due-time decisions are deterministic in UTC."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import SystemConfig
from app.services.backup_schedule import automatic_backup_due, parse_backup_time


class TestParseBackupTime:
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("00:00", id="start-of-day"),
            pytest.param("23:59", id="end-of-day"),
        ],
    )
    def test_accepts_a_minute_of_the_utc_day(self, value: str) -> None:
        assert parse_backup_time(value).isoformat(timespec="minutes") == value

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("24:00", id="hour-overflow"),
            pytest.param("02:00:00", id="seconds"),
            pytest.param("2:00", id="short-hour"),
            pytest.param("invalid", id="text"),
        ],
    )
    def test_rejects_a_value_that_is_not_an_hh_mm_time(self, value: str) -> None:
        with pytest.raises(ValueError, match="automatic_backup_time_invalid"):
            parse_backup_time(value)


class TestAutomaticBackupDue:
    @pytest.mark.parametrize(
        "config",
        [
            pytest.param(None, id="missing-config"),
            pytest.param(SystemConfig(automatic_backups_enabled=False), id="disabled"),
            pytest.param(
                SystemConfig(
                    automatic_backups_enabled=True,
                    automatic_backup_time_utc="12:00",
                ),
                id="before-time",
            ),
            pytest.param(
                SystemConfig(
                    automatic_backups_enabled=True,
                    automatic_backup_time_utc="02:00",
                    automatic_backup_last_attempt_at=datetime(2026, 9, 3, 2, 1),
                ),
                id="already-attempted",
            ),
        ],
    )
    def test_declines_a_schedule_that_is_not_due(
        self, config: SystemConfig | None
    ) -> None:
        now = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)

        assert automatic_backup_due(config, now) is False

    def test_accepts_an_unattempted_schedule_after_its_time(self) -> None:
        config = SystemConfig(
            automatic_backups_enabled=True,
            automatic_backup_time_utc="02:00",
        )

        assert (
            automatic_backup_due(
                config, datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
            )
            is True
        )
