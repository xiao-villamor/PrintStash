"""Reading a cron schedule to decide whether a library should be scanned now.

`is_due` is asked on every scheduler tick, so the question it answers is not
"does the cron expression match this minute?" — the tick would have to land on the
exact minute — but "has a scheduled boundary passed since the last scan?". That
framing is what makes a scan survive a restart or a tick that ran late: the
boundary is still in the past, so the scan still happens.

The two non-obvious answers are both refusals. An empty schedule means
manual-only and is never due, because an unset field must not silently become
"scan constantly". An unparseable expression is never due either, which is the
defensive choice: a library that quietly stops scanning is visible in its
last-scanned timestamp, whereas treating a typo as "always due" would poll the
user's NAS in a loop.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services import external_library

NOW = datetime(2026, 6, 15, 12, 30, tzinfo=timezone.utc)
HOURLY = "0 * * * *"


class TestIsDue:
    def test_reports_a_library_that_has_never_been_scanned_as_due(self) -> None:
        assert external_library.is_due(HOURLY, None, NOW) is True

    def test_reports_a_library_as_due_once_a_boundary_has_passed(self) -> None:
        last_scan = datetime(2026, 6, 15, 11, 0, tzinfo=timezone.utc)

        # The 12:00 boundary is behind us, and the tick asking is at 12:30 — a
        # scheduler that only fired exactly on the boundary would skip the scan.
        assert external_library.is_due(HOURLY, last_scan, NOW) is True

    def test_reports_a_library_as_not_due_before_the_next_boundary(self) -> None:
        last_scan = datetime(2026, 6, 15, 12, 20, tzinfo=timezone.utc)

        assert external_library.is_due(HOURLY, last_scan, NOW) is False

    def test_never_reports_a_manual_only_library_as_due(self) -> None:
        # An empty schedule must not read as "scan constantly".
        assert external_library.is_due("", None, NOW) is False

    def test_never_reports_a_library_with_an_unparseable_schedule_as_due(self) -> None:
        assert external_library.is_due("nope", None, NOW) is False
