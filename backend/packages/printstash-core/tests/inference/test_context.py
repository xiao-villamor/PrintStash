"""A provider operation has one monotonic deadline and caller cancellation fence."""

import time

import pytest

from printstash_core.inference import EmbeddingError
from printstash_core.inference.context import InferenceContext


class TestInferenceContext:
    def test_reports_remaining_budget(self):
        context = InferenceContext.bounded(10)

        assert 0 < context.remaining() <= 10

    def test_rejects_cancellation(self):
        context = InferenceContext.bounded(cancelled=lambda: True)

        with pytest.raises(EmbeddingError, match="inference_cancelled"):
            context.remaining()

    def test_rejects_expired_operations(self):
        context = InferenceContext(time.monotonic() - 1)

        with pytest.raises(EmbeddingError, match="inference_timeout"):
            context.remaining()

    @pytest.mark.parametrize(
        "seconds",
        [0, -1, 121, float("nan"), float("inf")],
        ids=["zero", "negative", "over-cap", "nan", "infinity"],
    )
    def test_rejects_invalid_deadlines(self, seconds):
        with pytest.raises(EmbeddingError, match="inference_deadline_invalid"):
            InferenceContext.bounded(seconds)
