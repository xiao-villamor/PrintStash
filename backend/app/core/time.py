"""Compatibility facade for framework-neutral UTC helpers."""

from printstash_core.time import ensure_utc as ensure_utc
from printstash_core.time import utcnow as utcnow

__all__ = ["ensure_utc", "utcnow"]
