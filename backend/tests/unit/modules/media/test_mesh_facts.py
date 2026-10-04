"""Sampling causes belong to the explicit prepared representation contract."""

import pytest

from app.modules.media.mesh_facts import FingerprintFailureCode, SampledGeometry


class TestSampledGeometry:
    @pytest.mark.parametrize(
        "reason", [FingerprintFailureCode.INVALID_SOURCE, "sampled_source", None]
    )
    def test_rejects_non_sampling_reason(self, reason):
        with pytest.raises((TypeError, ValueError), match="sampled_geometry"):
            SampledGeometry(reason)
