"""Timezone-aware UTC helpers."""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["ensure_utc", "utcnow"]


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Normalize a naive or aware ``datetime`` to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
