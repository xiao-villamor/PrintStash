"""Timezone-aware UTC helpers and strict wall-clock minute parsing."""

from __future__ import annotations

import re
from datetime import datetime, time, timezone

__all__ = ["ensure_utc", "parse_hh_mm_time", "utcnow"]


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Normalize a naive or aware ``datetime`` to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_hh_mm_time(value: str) -> time:
    """Parse an ASCII HH:MM minute within a day, without timezone conversion."""
    if re.fullmatch(r"[0-9]{2}:[0-9]{2}", value) is None:
        raise ValueError("time_hh_mm_invalid")
    hour = int(value[:2])
    minute = int(value[3:])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("time_hh_mm_invalid")
    return time(hour=hour, minute=minute)
