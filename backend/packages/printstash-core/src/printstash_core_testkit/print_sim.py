"""Wall-clock print simulation independent of an HTTP framework."""

from __future__ import annotations

import time
from collections.abc import Callable

STANDBY = "standby"
PRINTING = "printing"
PAUSED = "paused"
COMPLETE = "complete"
CANCELLED = "cancelled"
ERROR = "error"


class PrintSim:
    """Deterministic printer state derived from elapsed monotonic time."""

    def __init__(
        self,
        *,
        total_mm: float,
        total_seconds: float,
        print_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.total_mm = total_mm
        self.total_seconds = total_seconds
        self.speed = total_seconds / max(print_seconds, 1e-3)
        self.state = STANDBY
        self.filename = ""
        self.message = ""
        self._monotonic = monotonic
        self._started: float | None = None
        self._accumulated = 0.0

    def elapsed(self) -> float:
        if self.state == PRINTING and self._started is not None:
            return self._accumulated + (self._monotonic() - self._started) * self.speed
        return self._accumulated

    def progress(self) -> float:
        elapsed = self.elapsed()
        if (
            self.state == PRINTING
            and self.total_seconds
            and elapsed / self.total_seconds >= 1.0
        ):
            self.state = COMPLETE
            self._accumulated = self.total_seconds
            self._started = None
        if self.state == COMPLETE:
            return 1.0
        return min(elapsed / self.total_seconds, 1.0) if self.total_seconds else 0.0

    def filament_used(self) -> float:
        return round(self.total_mm * self.progress(), 4)

    def is_active(self) -> bool:
        return self.state in (PRINTING, PAUSED)

    def start(self, filename: str) -> None:
        self.filename = filename
        self.message = ""
        self.state = PRINTING
        self._started = self._monotonic()
        self._accumulated = 0.0

    def pause(self) -> None:
        if self.state == PRINTING:
            self._accumulated = self.elapsed()
            self._started = None
            self.state = PAUSED

    def resume(self) -> None:
        if self.state == PAUSED:
            self._started = self._monotonic()
            self.state = PRINTING

    def cancel(self) -> None:
        if self.state in (PRINTING, PAUSED):
            self._accumulated = self.elapsed()
            self._started = None
            self.state = CANCELLED

    def fail(self, message: str = "simulated failure") -> None:
        self._accumulated = self.elapsed()
        self._started = None
        self.state = ERROR
        self.message = message
