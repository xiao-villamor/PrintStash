"""Volume evidence distinguishes rejected integrals from unassessed geometry."""

import math

import pytest

from printstash_core.mesh.measurements import (
    VolumeLegacyUnassessed,
    VolumeMeasured,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeUnavailable,
    VolumeUnavailableCause,
    decode_volume,
    encode_volume,
    volume_value,
)


class TestVolumeMeasured:
    @pytest.mark.parametrize("value", [1e-9, 6e-9, 1000.0], ids=str)
    def test_preserves_small_positive_volume(self, value):
        result = VolumeMeasured(value)

        assert result.value_mm3 == value

    @pytest.mark.parametrize(
        "value",
        [0.0, -1.0, math.inf, -math.inf, math.nan, True, "1", None, 10**400],
        ids=str,
    )
    def test_rejects_invalid_measured_volume(self, value):
        with pytest.raises(ValueError):
            VolumeMeasured(value)


class TestVolumeUnavailable:
    @pytest.mark.parametrize("cause", ["inconsistent_winding", None, 1], ids=str)
    def test_rejects_non_enum_unavailable_cause(self, cause):
        with pytest.raises(ValueError):
            VolumeUnavailable(cause)


class TestVolumeNotCalculated:
    @pytest.mark.parametrize("cause", ["topology_not_evaluated", None, 1], ids=str)
    def test_rejects_non_enum_not_calculated_cause(self, cause):
        with pytest.raises(ValueError):
            VolumeNotCalculated(cause)


class TestVolumeLegacyUnassessed:
    @pytest.mark.parametrize("value", [None, 0.0, 500.0], ids=str)
    def test_preserves_legacy_unassessed_scalar(self, value):
        result = VolumeLegacyUnassessed(value)

        assert result.value_mm3 == value


class TestVolumeValue:
    @pytest.mark.parametrize("volume", [None, "measured", 1], ids=str)
    def test_rejects_invalid_volume_measurement(self, volume):
        with pytest.raises(TypeError, match="invalid volume measurement"):
            volume_value(volume)


class TestVolumeWire:
    @pytest.mark.parametrize("volume", [None, "measured", 1], ids=str)
    def test_rejects_invalid_volume_measurement(self, volume):
        with pytest.raises(TypeError, match="invalid volume measurement"):
            encode_volume(volume)

    @pytest.mark.parametrize(
        "volume",
        [
            VolumeMeasured(1e-9),
            *[VolumeUnavailable(cause) for cause in VolumeUnavailableCause],
            *[VolumeNotCalculated(cause) for cause in VolumeNotCalculatedCause],
            VolumeLegacyUnassessed(None),
            VolumeLegacyUnassessed(0.0),
            VolumeLegacyUnassessed(500.0),
        ],
        ids=str,
    )
    def test_preserves_each_volume_wire_variant(self, volume):
        encoded = encode_volume(volume)

        assert encoded["unit"] == "mm3"
        assert decode_volume(encoded) == volume

    @pytest.mark.parametrize("state", ["unknown", "", None, 1], ids=str)
    def test_rejects_unknown_volume_wire_variant(self, state):
        encoded = encode_volume(VolumeMeasured(1.0))
        encoded["state"] = state

        with pytest.raises(ValueError):
            decode_volume(encoded)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("value_mm3", 0.0),
            ("value_mm3", True),
            ("value_mm3", math.nan),
            ("value_mm3", 10**400),
            ("value_mm3", None),
            ("cause", "inconsistent_winding"),
            ("method", None),
            ("unit", "cm3"),
            ("extra", "unexpected"),
        ],
        ids=str,
    )
    def test_rejects_invalid_volume_wire_shape(self, field, value):
        encoded = encode_volume(VolumeMeasured(1.0))
        encoded[field] = value

        with pytest.raises(ValueError):
            decode_volume(encoded)
