"""Public volume evidence requires a coherent disjoint variant and scalar."""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.mesh_measurements import VolumeMeasurementRead
from app.schemas.models import MetadataRead

MEASURED = {
    "state": "measured",
    "unit": "mm3",
    "method": "mesh_surface_integral",
    "value_mm3": 6e-9,
    "cause": None,
}


class TestPublicVolumeEvidence:
    @pytest.mark.parametrize(
        "wire",
        [
            MEASURED,
            {
                "state": "unavailable",
                "unit": "mm3",
                "method": "mesh_surface_integral",
                "value_mm3": None,
                "cause": "inconsistent_winding",
            },
            {
                "state": "not_calculated",
                "unit": "mm3",
                "method": None,
                "value_mm3": None,
                "cause": "topology_not_evaluated",
            },
            {
                "state": "legacy_unassessed",
                "unit": "mm3",
                "method": None,
                "value_mm3": 0.0,
                "cause": None,
            },
            {
                "state": "legacy_unassessed",
                "unit": "mm3",
                "method": None,
                "value_mm3": -1.0,
                "cause": None,
            },
            {
                "state": "legacy_unassessed",
                "unit": "mm3",
                "method": None,
                "value_mm3": None,
                "cause": None,
            },
        ],
    )
    def test_preserves_public_volume_evidence(self, wire):
        read = MetadataRead.model_validate(
            {"volume_measurement": wire, "volume_mm3": wire["value_mm3"]}
        )
        assert read.model_dump(mode="json")["volume_measurement"] == wire
        assert read.volume_mm3 == wire["value_mm3"]

    @pytest.mark.parametrize("field", ["state", "unit", "method", "value_mm3", "cause"])
    def test_requires_every_volume_variant_field(self, field):
        wire = dict(MEASURED)
        del wire[field]
        with pytest.raises(ValidationError):
            TypeAdapter(VolumeMeasurementRead).validate_python(wire)

    @pytest.mark.parametrize(
        "changes",
        [
            {"state": "unknown"},
            {"unit": "cm3"},
            {"method": None},
            {"cause": "not_watertight"},
            {"extra": True},
            {"value_mm3": True},
            {"value_mm3": float("inf")},
            {"value_mm3": float("nan")},
            {"value_mm3": -1.0},
            {"value_mm3": 0.0},
            {"value_mm3": "6e-9"},
            {"value_mm3": 10**400},
            {"state": "unavailable", "value_mm3": None, "cause": "enrichment_pending"},
            {
                "state": "not_calculated",
                "value_mm3": None,
                "method": None,
                "cause": "not_watertight",
            },
            {"state": "legacy_unassessed", "cause": None},
        ],
    )
    def test_rejects_incompatible_or_nonfinite_public_volume(self, changes):
        with pytest.raises(ValidationError):
            TypeAdapter(VolumeMeasurementRead).validate_python(MEASURED | changes)

    def test_metadata_api_requires_volume_evidence(self):
        with pytest.raises(ValidationError, match="volume_measurement"):
            MetadataRead.model_validate({"volume_mm3": None})

    @pytest.mark.parametrize("scalar", [None, 0.0, 1.0, True, float("inf")])
    def test_metadata_api_rejects_scalar_disagreement(self, scalar):
        with pytest.raises(ValidationError):
            MetadataRead.model_validate(
                {"volume_measurement": MEASURED, "volume_mm3": scalar}
            )


class TestPublicDimensionEvidence:
    @pytest.mark.parametrize("axis", ["bbox_x_mm", "bbox_y_mm", "bbox_z_mm"])
    @pytest.mark.parametrize(
        "value", [float("inf"), float("nan"), -1.0, True, "1", 10**400]
    )
    def test_rejects_nonphysical_public_dimensions(self, axis, value):
        with pytest.raises(ValidationError):
            MetadataRead.model_validate(
                {"volume_measurement": MEASURED, "volume_mm3": 6e-9, axis: value}
            )

    @pytest.mark.parametrize("value", [0.0, 1e-9, None])
    def test_preserves_nullable_nonnegative_dimension_precision(self, value):
        read = MetadataRead.model_validate(
            {"volume_measurement": MEASURED, "volume_mm3": 6e-9, "bbox_x_mm": value}
        )
        assert read.bbox_x_mm == value
