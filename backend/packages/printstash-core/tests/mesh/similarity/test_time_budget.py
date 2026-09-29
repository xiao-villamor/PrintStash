"""Verification deadlines reject invalid limits and stop exhausted work."""

import math

import pytest

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.time_budget import check_deadline, deadline_after


class TestDeadlineAfter:
    def test_accepts_positive_verification_budget(self):
        deadline = deadline_after(10)

        check_deadline(deadline)

    @pytest.mark.parametrize("seconds", [0, -1, True, math.inf, math.nan], ids=str)
    def test_rejects_invalid_verification_budget(self, seconds):
        with pytest.raises(GeometryError, match="invalid_verification_budget"):
            deadline_after(seconds)


class TestCheckDeadline:
    def test_rejects_expired_verification_deadline(self):
        with pytest.raises(GeometryError, match="verification_time_limit"):
            check_deadline(0.0)
