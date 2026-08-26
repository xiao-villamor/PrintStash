"""Defends ``test_utcnow_returns_timezone_aware_utc`` behavior for the ``core`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from printstash_core.time import ensure_utc, utcnow


def test_utcnow_returns_timezone_aware_utc() -> None:
    before = datetime.now(timezone.utc)
    current = utcnow()
    after = datetime.now(timezone.utc)

    assert current.tzinfo is timezone.utc
    assert before <= current <= after


def test_ensure_utc_attaches_utc_to_naive_datetime() -> None:
    naive = datetime(2026, 8, 20, 12, 30, 45)

    normalized = ensure_utc(naive)

    assert normalized == datetime(2026, 8, 20, 12, 30, 45, tzinfo=timezone.utc)
    assert normalized.tzinfo is timezone.utc


def test_ensure_utc_converts_aware_datetime() -> None:
    source = datetime(
        2026,
        8,
        20,
        12,
        30,
        tzinfo=timezone(timedelta(hours=2)),
    )

    normalized = ensure_utc(source)

    assert normalized == datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc)
    assert normalized.tzinfo is timezone.utc
