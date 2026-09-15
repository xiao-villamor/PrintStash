"""Turn relative calendar periods into absolute half-open UTC intervals."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.time import ensure_utc

PERIODS = (
    "last_month",
    "this_month",
    "last_week",
    "this_week",
    "today",
    "yesterday",
    "past_30_days",
)


def bounds(period: str, now: datetime, timezone: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone)
    current = ensure_utc(now).astimezone(zone)
    today = current.date()
    if period in ("last_month", "this_month"):
        start = today.replace(day=1)
        if period == "last_month":
            end, start = start, (start - timedelta(days=1)).replace(day=1)
        else:
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    elif period in ("last_week", "this_week"):
        start = today - timedelta(days=today.weekday())
        if period == "last_week":
            end, start = start, start - timedelta(days=7)
        else:
            end = start + timedelta(days=7)
    elif period in ("today", "yesterday"):
        start = today - timedelta(days=1 if period == "yesterday" else 0)
        end = start + timedelta(days=1)
    elif period == "past_30_days":
        return ensure_utc(current - timedelta(days=30)), ensure_utc(current)
    else:
        raise ValueError("search_period_invalid")
    return tuple(
        ensure_utc(datetime.combine(day, time.min, tzinfo=zone)) for day in (start, end)
    )
