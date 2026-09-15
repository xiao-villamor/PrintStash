"""Portable proof values preserve scalar precision across database dialects."""

from datetime import date
from decimal import Decimal

import pytest

from app.modules.administration.database_transfer import _canonical


class TestCanonical:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (date(2026, 1, 1), "2026-01-01"),
            (Decimal("1234567890.123456789000"), "1234567890.123456789"),
        ],
        ids=["date", "decimal"],
    )
    def test_preserves_portable_scalar_proof_values(self, value, expected):
        assert _canonical(value) == expected
