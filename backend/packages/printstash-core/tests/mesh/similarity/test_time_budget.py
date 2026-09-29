"""Verification deadlines reject invalid limits and stop exhausted work."""

import math

import pytest

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.time_budget import check_deadline, deadline_after


def test_accepts_positive_verification_budget():
    deadline = deadline_after(10)

    check_deadline(deadline)


@pytest.mark.parametrize("seconds", [0, -1, True, math.inf, math.nan], ids=str)
def test_rejects_invalid_verification_budget(seconds):
    with pytest.raises(GeometryError, match="invalid_verification_budget"):
        deadline_after(seconds)


def test_rejects_expired_verification_deadline():
    with pytest.raises(GeometryError, match="verification_time_limit"):
        check_deadline(0.0)
