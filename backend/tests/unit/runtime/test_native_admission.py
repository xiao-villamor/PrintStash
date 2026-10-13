"""Invalid resource amounts must not enter the shared admission ledger."""

import pytest

from app.runtime.native_admission import Resources


class TestResources:
    @pytest.mark.parametrize(
        "value", [0, -1, True, 1.5], ids=["zero", "negative", "boolean", "float"]
    )
    @pytest.mark.parametrize("field", ["slots", "bytes"])
    def test_rejects_invalid_amount(self, value, field):
        amounts = {"slots": 1, "bytes": 100}
        amounts[field] = value

        with pytest.raises(ValueError, match="positive integers"):
            Resources(**amounts)

    @pytest.mark.parametrize(
        "amount",
        [Resources(1, 100), Resources(2, 50)],
        ids=["byte-boundary", "slot-boundary"],
    )
    def test_fits_each_capacity_boundary(self, amount):
        assert amount.fits(Resources(2, 100)) is True

    @pytest.mark.parametrize(
        "amount",
        [Resources(1, 101), Resources(3, 50)],
        ids=["bytes-over", "slots-over"],
    )
    def test_refuses_either_capacity_overflow(self, amount):
        assert amount.fits(Resources(2, 100)) is False
