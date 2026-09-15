"""Inference cancellation and a single monotonic operation budget."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from .embedding import EmbeddingError


@dataclass(frozen=True)
class InferenceContext:
    deadline: float
    cancelled: Callable[[], bool] = field(
        default=lambda: False, repr=False, compare=False
    )
    priority: Literal["interactive", "background"] = "interactive"

    @classmethod
    def bounded(
        cls,
        seconds: float = 15,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        priority: Literal["interactive", "background"] = "interactive",
    ) -> InferenceContext:
        if not 0 < seconds <= 120:
            raise EmbeddingError("inference_deadline_invalid")
        return cls(time.monotonic() + seconds, cancelled, priority)

    def remaining(self) -> float:
        if self.cancelled():
            raise EmbeddingError("inference_cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise EmbeddingError("inference_timeout")
        return remaining
