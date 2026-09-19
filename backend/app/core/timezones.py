"""IANA timezone validation shared by instance and personal preferences."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def timezone_name(value: str) -> str:
    if not value or len(value) > 128:
        raise ValueError("timezone_invalid")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone_invalid") from None
    return value
