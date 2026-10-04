"""Geometry tags preserve refused measurements independently of usable previews.

These pure data contracts are shared by the native worker and its supervisor;
unknown tags or reasons must fail instead of turning unavailable geometry into
an apparently successful measurement.
"""

from __future__ import annotations

import pytest
from printstash_core.mesh.measurements import (
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
)

from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryNotRequested,
    GeometryReady,
    GeometryRefused,
    MeshCoverage,
    MeshMeasurements,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    decode_coverage,
    decode_geometry,
    encode_coverage,
    encode_geometry,
)
from app.modules.media.mesh_facts import (
    CompleteGeometry,
    FingerprintFailureCode,
    SampledGeometry,
)


class TestEncodeGeometry:
    @pytest.mark.parametrize(
        ("outcome", "encoded"),
        [
            (GeometryReady(), {"state": "ready"}),
            (GeometryNotRequested(), {"state": "not_requested"}),
            (
                GeometryRefused(ThumbnailFailureReason.RESOURCE_LIMIT),
                {"state": "refused", "reason": "resource_limit"},
            ),
        ],
    )
    def test_preserves_the_geometry_wire_shape(self, outcome, encoded):
        assert encode_geometry(outcome) == encoded

    def test_rejects_an_unknown_geometry_outcome(self):
        with pytest.raises(TypeError, match="invalid geometry outcome"):
            encode_geometry(object())


class TestDecodeGeometry:
    @pytest.mark.parametrize(
        ("encoded", "outcome"),
        [
            ({"state": "ready"}, GeometryReady()),
            ({"state": "not_requested"}, GeometryNotRequested()),
            *[
                ({"state": "refused", "reason": reason.value}, GeometryRefused(reason))
                for reason in ThumbnailFailureReason
            ],
        ],
    )
    def test_decodes_each_geometry_outcome(self, encoded, outcome):
        assert decode_geometry(encoded) == outcome

    @pytest.mark.parametrize(
        "encoded",
        [
            {},
            {"state": "unknown"},
            {"state": "ready", "reason": "resource_limit"},
            {"state": "not_requested", "extra": "value"},
            {"state": "refused"},
            {"state": "refused", "reason": "resource_limit", "extra": "value"},
        ],
    )
    def test_rejects_an_unknown_geometry_tag(self, encoded):
        with pytest.raises(ValueError, match="invalid geometry outcome"):
            decode_geometry(encoded)

    @pytest.mark.parametrize("reason", ["unknown", "", None, 1])
    def test_rejects_an_unknown_refusal_reason(self, reason):
        with pytest.raises(ValueError, match="is not a valid ThumbnailFailureReason"):
            decode_geometry({"state": "refused", "reason": reason})


class TestMeshCoverage:
    @pytest.mark.parametrize(
        ("source_scan", "geometry", "preview"),
        [
            (
                SourceScanState.NOT_SCANNED,
                GeometryNotLoaded(),
                PreviewCoverage.NOT_PRODUCED,
            ),
            (
                SourceScanState.NOT_SCANNED,
                GeometryNotLoaded(),
                PreviewCoverage.DOCUMENT_SUPPLIED,
            ),
            (SourceScanState.COMPLETE, CompleteGeometry(), PreviewCoverage.COMPLETE),
            (SourceScanState.COMPLETE, GeometryNotLoaded(), PreviewCoverage.COMPLETE),
            (SourceScanState.COMPLETE, CompleteGeometry(), PreviewCoverage.PARTIAL),
            (
                SourceScanState.COMPLETE,
                SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
                PreviewCoverage.NOT_PRODUCED,
            ),
            (
                SourceScanState.PARTIAL,
                SampledGeometry(FingerprintFailureCode.SAMPLED_OVERSIZED_SOURCE),
                PreviewCoverage.PARTIAL,
            ),
        ],
        ids=[
            "no-output",
            "document-preview",
            "full-mesh",
            "streamed-source",
            "partial-preview",
            "sampled-analysis",
            "sampled-source",
        ],
    )
    def test_round_trips_independent_facts(self, source_scan, geometry, preview):
        coverage = MeshCoverage(source_scan, geometry, preview)

        assert decode_coverage(encode_coverage(coverage)) == coverage

    @pytest.mark.parametrize(
        "source_scan",
        [SourceScanState.NOT_SCANNED, SourceScanState.PARTIAL],
        ids=["unread", "partial"],
    )
    def test_rejects_full_geometry_without_complete_scan(self, source_scan):
        with pytest.raises(
            ValueError, match="complete geometry requires a complete source scan"
        ):
            MeshCoverage(source_scan, CompleteGeometry(), PreviewCoverage.NOT_PRODUCED)

    def test_rejects_sampled_geometry_without_scan(self):
        with pytest.raises(ValueError, match="sampled geometry requires a source scan"):
            MeshCoverage(
                SourceScanState.NOT_SCANNED,
                SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
                PreviewCoverage.NOT_PRODUCED,
            )

    @pytest.mark.parametrize("field", ["source_scan", "geometry", "preview"], ids=str)
    def test_rejects_untyped_facts(self, field):
        values = {
            "source_scan": SourceScanState.COMPLETE,
            "geometry": CompleteGeometry(),
            "preview": PreviewCoverage.COMPLETE,
        }
        values[field] = "complete"

        with pytest.raises(TypeError, match="invalid"):
            MeshCoverage(**values)


class TestDecodeCoverage:
    @pytest.mark.parametrize("field", ["source_scan", "geometry", "preview"], ids=str)
    def test_requires_each_fact(self, field):
        encoded = encode_coverage(
            MeshCoverage(
                SourceScanState.COMPLETE, CompleteGeometry(), PreviewCoverage.COMPLETE
            )
        )
        del encoded[field]

        with pytest.raises(ValueError, match="invalid mesh coverage"):
            decode_coverage(encoded)

    @pytest.mark.parametrize("field", ["source_scan", "preview"], ids=str)
    @pytest.mark.parametrize(
        "value",
        ["unknown", "", None, True, 1],
        ids=["unknown", "empty", "null", "boolean", "number"],
    )
    def test_rejects_unknown_enum_fact(self, field, value):
        encoded = encode_coverage(
            MeshCoverage(
                SourceScanState.COMPLETE, CompleteGeometry(), PreviewCoverage.COMPLETE
            )
        )
        encoded[field] = value

        with pytest.raises(ValueError, match="is not a valid"):
            decode_coverage(encoded)

    @pytest.mark.parametrize(
        "geometry",
        [
            {},
            {"state": "unknown"},
            {"state": "complete", "extra": True},
            {"state": "sampled"},
        ],
        ids=["missing", "unknown", "extra", "missing-reason"],
    )
    def test_rejects_unknown_geometry_fact(self, geometry):
        encoded = {
            "source_scan": "complete",
            "geometry": geometry,
            "preview": "complete",
        }

        with pytest.raises(ValueError, match="invalid geometry coverage"):
            decode_coverage(encoded)

    def test_rejects_non_sampling_cause(self):
        encoded = {
            "source_scan": "partial",
            "geometry": {"state": "sampled", "reason": "invalid_source"},
            "preview": "partial",
        }

        with pytest.raises(ValueError, match="invalid_sampled_geometry_reason"):
            decode_coverage(encoded)


class TestMeshMeasurements:
    def test_unavailable_measurements_preserve_typed_geometry_evidence(self):
        measurements = MeshMeasurements.unavailable()

        assert measurements.geometry == {
            "bbox_x_mm": None,
            "bbox_y_mm": None,
            "bbox_z_mm": None,
            "volume_mm3": None,
            "triangle_count": None,
        }
        assert measurements.volume == VolumeNotCalculated(
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
        )
