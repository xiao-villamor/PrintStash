"""Trash expiry is derived solely from deletion time and retention policy.

Keeping this helper deterministic makes API expiry labels and GC eligibility
agree at the exact retention boundary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.trash import trash_expires_at

DELETED_AT = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class TestTrashExpiresAt:
    def test_adds_the_retention_window_to_the_deletion_instant(self) -> None:
        assert trash_expires_at(DELETED_AT, 30) == DELETED_AT + timedelta(days=30)

    def test_zero_retention_expires_at_the_deletion_instant(self) -> None:
        assert trash_expires_at(DELETED_AT, 0) == DELETED_AT

    def test_negative_retention_disables_expiry(self) -> None:
        assert trash_expires_at(DELETED_AT, -1) is None

    def test_live_resources_have_no_expiry(self) -> None:
        assert trash_expires_at(None, 30) is None
