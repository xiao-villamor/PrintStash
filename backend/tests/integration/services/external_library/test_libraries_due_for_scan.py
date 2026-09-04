"""`libraries_due_for_scan` picking what the periodic scheduler should touch.

The scheduler polls far more often than any library's schedule, so this function
is what stops every tick from rescanning everything over the network. It has to
exclude three separate things, each for its own reason: a disabled library (the
user turned it off), a library whose cron boundary has not passed (that is what
the schedule means), and a library with an empty schedule (manual-only — a scan
happens when someone asks for one and never otherwise).

Returning too much here is not a wrong answer that surfaces as a test failure; it
is a NAS getting hammered on a loop."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    ExternalLibraryScanStatus,
)
from app.services import external_library
from tests.factories import build_external_library
from tests.integration.services.external_library._helpers import (
    enable_feature,
)


class TestLibrariesDueForScan:
    def test_scheduler_selects_only_due_libraries(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        enable_feature(db_session)
        now = utcnow()

        never = build_external_library(
            db_session, tmp_path / "never", name="nas", enabled=True
        )
        manual = build_external_library(
            db_session,
            tmp_path / "manual",
            name="nas",
            enabled=True,
            scan_schedule="",  # manual only → never auto-due
            last_scanned_at=now - timedelta(hours=2),
        )
        stale = build_external_library(
            db_session,
            tmp_path / "stale",
            name="nas",
            enabled=True,
            scan_schedule="0 * * * *",  # hourly; 2h elapsed → a boundary has passed
            last_scanned_at=now - timedelta(hours=2),
        )
        disabled = build_external_library(
            db_session, tmp_path / "disabled", name="nas", enabled=False
        )
        running = build_external_library(
            db_session,
            tmp_path / "running",
            name="nas",
            enabled=True,
            last_scan_status=ExternalLibraryScanStatus.RUNNING,
        )

        due = external_library.libraries_due_for_scan(db_session)

        assert never.id in due  # never scanned → due immediately
        assert stale.id in due  # cron boundary elapsed → due
        assert manual.id not in due  # manual only → never auto-due
        assert disabled.id not in due  # disabled → never
        assert running.id not in due  # already scanning → skipped
