"""A wall-clock ceiling for one geometric verification, checked within its loops."""

from __future__ import annotations

import math
import time

from .fingerprint import GeometryError


def deadline_after(seconds: float) -> float:
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise GeometryError("invalid_verification_budget")
    return time.monotonic() + seconds


def check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise GeometryError("verification_time_limit")
