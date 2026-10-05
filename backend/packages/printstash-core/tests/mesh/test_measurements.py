"""Volume evidence distinguishes rejected integrals from unassessed geometry."""

import math

import pytest

from printstash_core.mesh.measurements import (
    InvalidMeshMeasurements,
    VolumeLegacyUnassessed,
    VolumeMeasured,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeUnavailable,
    VolumeUnavailableCause,
    decode_volume,
    encode_volume,
    validate_geometry_extents,
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

    @pytest.mark.parametrize("value", [None, 0, -1.5, 500.0], ids=str)
    def test_scalar_factory_keeps_measurement_unassessed(self, value):
        result = VolumeLegacyUnassessed.from_value(value)

        assert result.value_mm3 == value
        assert encode_volume(result)["state"] == "legacy_unassessed"
        assert encode_volume(result)["method"] is None

    @pytest.mark.parametrize("value", [True, "1", math.nan, 10**400], ids=str)
    def test_scalar_factory_rejects_nonfinite_legacy_input(self, value):
        with pytest.raises(ValueError, match="volume must be a finite number"):
            VolumeLegacyUnassessed.from_value(value)


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

    @pytest.mark.parametrize(
        ("field", "value"),
        [("value_mm3", 1.0), ("method", None), ("cause", 1)],
        ids=["scalar", "missing-method", "untyped-cause"],
    )
    def test_rejects_inconsistent_unavailable_evidence(self, field, value):
        encoded = encode_volume(
            VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT)
        )
        encoded[field] = value

        with pytest.raises(ValueError, match="invalid unavailable volume evidence"):
            decode_volume(encoded)

    @pytest.mark.parametrize(
        ("field", "value"),
        [("value_mm3", 0.0), ("method", "mesh_surface_integral"), ("cause", 1)],
        ids=["scalar", "invented-method", "untyped-cause"],
    )
    def test_rejects_inconsistent_not_calculated_evidence(self, field, value):
        encoded = encode_volume(
            VolumeNotCalculated(VolumeNotCalculatedCause.ENRICHMENT_PENDING)
        )
        encoded[field] = value

        with pytest.raises(ValueError, match="invalid not calculated volume evidence"):
            decode_volume(encoded)

    @pytest.mark.parametrize(
        ("field", "value"),
        [("method", "mesh_surface_integral"), ("cause", "not_watertight")],
        ids=["method", "cause"],
    )
    def test_rejects_legacy_scalar_with_measurement_evidence(self, field, value):
        encoded = encode_volume(VolumeLegacyUnassessed(500.0))
        encoded[field] = value

        with pytest.raises(ValueError, match="invalid legacy volume evidence"):
            decode_volume(encoded)


class TestGeometryExtents:
    @pytest.mark.parametrize(
        "geometry",
        [
            {},
            {"bbox_x_mm": None},
            {"bbox_x_mm": 0, "bbox_y_mm": 1e-300, "bbox_z_mm": 3.5},
            {"bbox_x_mm": 12.0, "triangle_count": -1},
        ],
        ids=["absent", "unassessed", "finite-nonnegative", "unrelated-metadata"],
    )
    def test_accepts_partial_finite_dimensions(self, geometry):
        original = dict(geometry)

        validate_geometry_extents(geometry)

        assert geometry == original

    @pytest.mark.parametrize("axis", ["bbox_x_mm", "bbox_y_mm", "bbox_z_mm"])
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(-1.0, id="negative"),
            pytest.param(math.inf, id="positive-infinity"),
            pytest.param(-math.inf, id="negative-infinity"),
            pytest.param(math.nan, id="nan"),
            pytest.param(True, id="boolean"),
            pytest.param("1", id="numeric-string"),
            pytest.param({}, id="mapping"),
            pytest.param(10**400, id="overflowing-integer"),
        ],
    )
    def test_rejects_unrepresentable_dimension(self, axis, value):
        geometry = {"bbox_x_mm": 1.0, "bbox_y_mm": 2.0, "bbox_z_mm": 3.0, axis: value}

        with pytest.raises(
            InvalidMeshMeasurements,
            match="mesh dimensions must be finite nonnegative numbers",
        ):
            validate_geometry_extents(geometry)
