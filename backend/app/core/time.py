"""Compatibility facade for framework-neutral clock and minute helpers."""

from printstash_core.time import ensure_utc as ensure_utc
from printstash_core.time import parse_hh_mm_time as parse_hh_mm_time
from printstash_core.time import utcnow as utcnow

__all__ = ["ensure_utc", "parse_hh_mm_time", "utcnow"]
