"""Rebasing uses known relative measurements, never geometry guesses."""

import pytest

from app.modules.library.families.relative import rebase_scale


class TestRebaseScale:
    @pytest.mark.parametrize(
        "factor,reference,expected",
        [(4.0, 2.0, 2.0), (1.0, 2.0, 0.5)],
        ids=["larger", "smaller"],
    )
    def test_rebases_relative_scale(self, factor, reference, expected):
        assert rebase_scale(factor, reference, same_reference=True) == expected

    @pytest.mark.parametrize(
        "factor,reference,same",
        [
            (None, 2.0, True),
            (2.0, None, True),
            (2.0, 1.0, False),
            (1e308, 1e-308, True),
        ],
        ids=["unmeasured", "unknown-reference", "different-base", "overflow"],
    )
    def test_clears_unreliable_relative_scale(self, factor, reference, same):
        assert rebase_scale(factor, reference, same_reference=same) is None
