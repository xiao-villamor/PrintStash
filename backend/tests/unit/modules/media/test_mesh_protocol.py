"""Worker frames expose only validated outputs in the exact requested order.

Earlier validated outputs survive a later malformed frame. Fingerprint terminal
acceptance needs the whole bounded stream; transport never decodes code.
"""

import json
import struct
from dataclasses import replace
from itertools import product
from pathlib import Path

import pytest
from printstash_core.mesh.measurements import (
    VolumeLegacyUnassessed,
    VolumeMeasured,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeUnavailable,
    VolumeUnavailableCause,
    encode_volume,
    volume_value,
)

from app.modules.media import mesh_protocol
from app.modules.media.fingerprints import (
    FingerprintRecord,
    FingerprintResult,
    FingerprintResultState,
)
from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryReady,
    GeometryRefused,
    MeshCoverage,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailStrategy,
    encode_coverage,
)
from app.modules.media.mesh_facts import (
    CompleteGeometry,
    FingerprintFailureCode,
    SampledGeometry,
)
from app.modules.media.mesh_protocol import (
    FingerprintFinal,
    FrameDecoder,
    FrameKind,
    GeometryOutput,
    MeshProtocolError,
    ThumbnailOutput,
    encode_frame,
)

_GEOMETRY_COVERAGE = MeshCoverage(
    SourceScanState.COMPLETE, GeometryNotLoaded(), PreviewCoverage.NOT_PRODUCED
)
_PREVIEW_COVERAGE = MeshCoverage(
    SourceScanState.COMPLETE, CompleteGeometry(), PreviewCoverage.COMPLETE
)


def _wire(kind, sequence, raw, image=b""):
    return (
        struct.pack("!4sBIII", b"MSH2", kind, sequence, len(raw), len(image))
        + raw
        + image
    )


def _forge(kind, sequence, values, image=b""):
    return _wire(kind, sequence, json.dumps(values).encode(), image)


@pytest.fixture
def geometry():
    return GeometryOutput(
        {
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "volume_mm3": 1000.0,
            "triangle_count": 4,
        },
        GeometryReady(),
        VolumeMeasured(1000.0),
        _GEOMETRY_COVERAGE,
        5,
        1000,
    )


@pytest.fixture
def thumbnail():
    return ThumbnailOutput(
        b"image", ThumbnailStrategy.FULL, _PREVIEW_COVERAGE, None, 10, 2000
    )


@pytest.fixture
def fingerprint():
    return FingerprintResult(
        FingerprintResultState.READY,
        (
            FingerprintRecord(
                0,
                1,
                {
                    "blob": b"descriptor",
                    "axes": (1.0, 2.0, 3.0),
                    "nested": {
                        "shape": [1, 2],
                        "reflected_transform": (
                            (-1.0, 0.0, 0.0, 40.0),
                            (0.0, 1.0, 0.0, 0.0),
                            (0.0, 0.0, 1.0, 0.0),
                            (0.0, 0.0, 0.0, 1.0),
                        ),
                    },
                },
                (),
            ),
        ),
    )


@pytest.fixture
def final(fingerprint):
    return FingerprintFinal(fingerprint, (), 20, 3000, _PREVIEW_COVERAGE)


@pytest.fixture
def request_all():
    return ThumbnailRequest(Path("mesh.3mf"), include_fingerprint=True)


@pytest.fixture
def stream(geometry, thumbnail, final):
    return (
        encode_frame(geometry, sequence=0)
        + encode_frame(thumbnail, sequence=1)
        + encode_frame(final, sequence=2)
    )


@pytest.fixture
def requested_outputs(flags, geometry, thumbnail, fingerprint):
    include_geometry, include_thumbnail, include_fingerprint = flags
    request = ThumbnailRequest(
        Path("mesh"),
        include_geometry=include_geometry,
        include_thumbnail=include_thumbnail,
        include_fingerprint=include_fingerprint,
    )
    final = FingerprintFinal(
        fingerprint if include_fingerprint else None, (), 20, 3000, _PREVIEW_COVERAGE
    )
    outputs = (
        *((geometry,) if include_geometry else ()),
        *((thumbnail,) if include_thumbnail else ()),
        final,
    )
    return request, outputs


@pytest.fixture
def state_final(state, fingerprint):
    if state is FingerprintResultState.READY:
        result, coverage = (
            fingerprint,
            MeshCoverage(
                SourceScanState.COMPLETE,
                CompleteGeometry(),
                PreviewCoverage.NOT_PRODUCED,
            ),
        )
    elif state is FingerprintResultState.PARTIAL:
        values = dict(
            fingerprint.records[0].values, recipe={"complete_geometry": False}
        )
        result = FingerprintResult(
            state,
            (FingerprintRecord(0, 1, values, ()),),
            FingerprintFailureCode.SAMPLED_SOURCE,
        )
        coverage = MeshCoverage(
            SourceScanState.COMPLETE,
            SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
            PreviewCoverage.NOT_PRODUCED,
        )
    else:
        result = FingerprintResult(state, (), FingerprintFailureCode.SOURCE_CHANGED)
        coverage = _GEOMETRY_COVERAGE
    return FingerprintFinal(result, (), 20, 3000, coverage)


class TestEncodeFrame:
    def test_preserves_fingerprint_tagged_values(self, final):
        decoder = FrameDecoder(
            ThumbnailRequest(
                Path("mesh"),
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )

        final = replace(
            final,
            coverage=MeshCoverage(
                SourceScanState.COMPLETE,
                CompleteGeometry(),
                PreviewCoverage.NOT_PRODUCED,
            ),
        )
        frames = tuple(decoder.feed(encode_frame(final, sequence=0)))

        assert frames == (final,)
        assert decoder.finish() == final
        assert frames[0].fingerprint.records[0].values["blob"] == b"descriptor"
        assert frames[0].fingerprint.records[0].values["axes"] == (1.0, 2.0, 3.0)
        assert frames[0].fingerprint.records[0].values["nested"]["reflected_transform"][
            0
        ] == (-1.0, 0.0, 0.0, 40.0)

    @pytest.mark.parametrize(
        "sequence",
        [-1, True, 1.5, 3],
        ids=["negative", "bool", "fractional", "too-many"],
    )
    def test_rejects_invalid_sequence(self, final, sequence):
        with pytest.raises((ValueError, TypeError)):
            encode_frame(final, sequence=sequence)


class TestFrameDecoder:
    @pytest.mark.parametrize("stride", [1, 17, 1000], ids=repr)
    def test_accepts_fragmented_frames(
        self, request_all, stream, geometry, thumbnail, final, stride
    ):
        decoder = FrameDecoder(request_all)

        frames = tuple(
            frame
            for start in range(0, len(stream), stride)
            for frame in decoder.feed(stream[start : start + stride])
        )

        assert frames == (geometry, thumbnail, final)
        assert decoder.finish() == final

    def test_accepts_combined_frames(
        self, request_all, stream, geometry, thumbnail, final
    ):
        decoder = FrameDecoder(request_all)

        frames = tuple(decoder.feed(stream))

        assert frames == (geometry, thumbnail, final)
        assert decoder.finish() == final

    @pytest.mark.parametrize("flags", list(product((False, True), repeat=3)), ids=repr)
    def test_accepts_requested_output_subsets(self, requested_outputs):
        request, outputs = requested_outputs
        decoder = FrameDecoder(request)
        stream = b"".join(
            encode_frame(frame, sequence=i) for i, frame in enumerate(outputs)
        )

        frames = tuple(decoder.feed(stream))

        assert frames == outputs
        assert decoder.finish() == outputs[-1]

    @pytest.mark.parametrize("which", ["thumbnail", "final"], ids=repr)
    def test_rejects_out_of_order_output(self, request_all, thumbnail, final, which):
        decoder = FrameDecoder(request_all)
        frame = {"thumbnail": thumbnail, "final": final}[which]

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(encode_frame(frame, sequence=0)))

    def test_rejects_replayed_output(self, request_all, geometry):
        decoder = FrameDecoder(request_all)
        encoded = encode_frame(geometry, sequence=0)
        first = tuple(decoder.feed(encoded))

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(encoded))

        assert first == (geometry,)

    @pytest.mark.parametrize("trailing", [b"x", b"MSH2"], ids=repr)
    def test_rejects_trailing_output(self, request_all, stream, final, trailing):
        decoder = FrameDecoder(request_all)
        frames = tuple(decoder.feed(stream))

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(trailing))

        assert frames[-1] == final

    @pytest.mark.parametrize("cut", [0, 1, 16, 20, -1], ids=repr)
    def test_rejects_incomplete_stream(self, request_all, stream, cut):
        decoder = FrameDecoder(request_all)
        tuple(decoder.feed(stream[:cut]))

        with pytest.raises(MeshProtocolError, match="incomplete"):
            decoder.finish()

    @pytest.mark.parametrize(
        "tag,header,image",
        [(1, 65537, 0), (1, 10, 1), (2, 10, 33554432), (3, 33554432, 0)],
        ids=repr,
    )
    def test_rejects_announced_oversize(self, tag, header, image):
        request = ThumbnailRequest(
            Path("mesh"),
            include_geometry=tag == 1,
            include_thumbnail=tag == 2,
            include_fingerprint=tag == 3,
        )
        decoder = FrameDecoder(request)
        prefix = struct.pack("!4sBIII", b"MSH2", tag, 0, header, image)

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(prefix))

    def test_rejects_aggregate_oversize(self, request_all, stream, monkeypatch):
        decoder = FrameDecoder(request_all)
        monkeypatch.setattr(mesh_protocol, "MAX_STREAM_BYTES", len(stream) - 1)

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(stream))

    @pytest.mark.parametrize(
        "field,value",
        [
            ("triangle_count", True),
            ("triangle_count", None),
            ("bbox_x_mm", float("nan")),
            ("volume_mm3", -1.0),
            ("bbox_y_mm", "20"),
            ("extra", 1),
        ],
        ids=repr,
    )
    def test_rejects_invalid_geometry_fields(self, request_all, geometry, field, value):
        decoder = FrameDecoder(request_all)
        values = dict(geometry.geometry)
        values[field] = value
        forged = _forge(
            FrameKind.GEOMETRY,
            0,
            {
                "geometry": values,
                "outcome": {"state": "ready"},
                "volume": encode_volume(VolumeMeasured(1000.0)),
                "coverage": encode_coverage(_GEOMETRY_COVERAGE),
                "duration_ms": 5,
                "peak_rss_bytes": 1000,
            },
        )

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(forged))

    @pytest.mark.parametrize(
        "field,value",
        [
            ("strategy", "warp"),
            ("coverage", True),
            ("failure_reason", "unknown"),
            ("duration_ms", True),
            ("peak_rss_bytes", -1),
        ],
        ids=repr,
    )
    def test_rejects_invalid_thumbnail_fields(self, field, value):
        decoder = FrameDecoder(ThumbnailRequest(Path("mesh"), include_geometry=False))
        header = {
            "strategy": "full",
            "coverage": encode_coverage(_PREVIEW_COVERAGE),
            "failure_reason": None,
            "has_image": True,
            "image_length": 5,
            "duration_ms": 10,
            "peak_rss_bytes": 1000,
        }
        header[field] = value
        forged = _forge(FrameKind.THUMBNAIL, 0, header, b"image")

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(forged))

    @pytest.mark.parametrize(
        "fingerprint",
        [
            None,
            {
                "state": "unknown",
                "failure_code": None,
                "algorithm_version": "geometry-v5",
                "records": [],
            },
            {
                "state": "ready",
                "failure_code": None,
                "algorithm_version": "geometry-v5",
                "records": [],
            },
            {
                "state": "failed",
                "failure_code": None,
                "algorithm_version": "geometry-v5",
                "records": [],
            },
        ],
        ids=repr,
    )
    def test_rejects_invalid_fingerprint_fields(self, fingerprint):
        decoder = FrameDecoder(
            ThumbnailRequest(
                Path("mesh"),
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )
        forged = _forge(
            FrameKind.FINAL,
            0,
            {
                "fingerprint": fingerprint,
                "phase_stats": {"version": 1, "stages": []},
                "duration_ms": 20,
                "peak_rss_bytes": 1000,
                "coverage": encode_coverage(_PREVIEW_COVERAGE),
            },
        )

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(forged))

    def test_rejects_duplicate_json_keys(self, request_all):
        decoder = FrameDecoder(request_all)
        forged = _wire(
            FrameKind.GEOMETRY, 0, b'{"outcome":{"state":"ready","state":"refused"}}'
        )

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(forged))

    def test_rejects_image_length_mismatch(self):
        decoder = FrameDecoder(ThumbnailRequest(Path("mesh"), include_geometry=False))
        forged = _forge(
            FrameKind.THUMBNAIL,
            0,
            {
                "strategy": "full",
                "coverage": encode_coverage(_PREVIEW_COVERAGE),
                "failure_reason": None,
                "has_image": True,
                "image_length": 6,
                "duration_ms": 10,
                "peak_rss_bytes": 1000,
            },
            b"image",
        )

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(forged))

    def test_preserves_earlier_output_before_invalid_terminal(
        self, request_all, geometry, thumbnail
    ):
        decoder = FrameDecoder(request_all)
        stream = (
            encode_frame(geometry, sequence=0)
            + encode_frame(thumbnail, sequence=1)
            + _forge(FrameKind.FINAL, 2, {})
        )
        outputs = decoder.feed(stream)

        assert next(outputs) == geometry
        assert next(outputs) == thumbnail
        with pytest.raises(MeshProtocolError):
            next(outputs)


class TestClosedSchemas:
    @pytest.mark.parametrize("state", list(FingerprintResultState), ids=repr)
    def test_round_trips_each_fingerprint_state(self, state, state_final):
        decoder = FrameDecoder(
            ThumbnailRequest(
                Path("mesh"),
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )
        encoded = encode_frame(state_final, sequence=0)
        body = json.loads(encoded[17:])

        decoded = tuple(decoder.feed(encoded))

        assert body["fingerprint"]["state"] == state.value
        assert type(body["fingerprint"]["state"]) is str
        assert decoded == (state_final,)
        assert decoder.finish().fingerprint.state is state

    @pytest.mark.parametrize("reason", list(ThumbnailFailureReason), ids=repr)
    def test_preserves_refusal_beside_successful_preview(self, reason, thumbnail):
        geometry = GeometryOutput(
            dict.fromkeys(
                ("bbox_x_mm", "bbox_y_mm", "bbox_z_mm", "volume_mm3", "triangle_count")
            ),
            GeometryRefused(reason),
            VolumeNotCalculated(VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE),
            MeshCoverage(
                SourceScanState.NOT_SCANNED,
                GeometryNotLoaded(),
                PreviewCoverage.NOT_PRODUCED,
            ),
            5,
            None,
        )
        decoder = FrameDecoder(ThumbnailRequest(Path("mesh")))
        final = FingerprintFinal(None, (), 20, None, _PREVIEW_COVERAGE)
        wire = (
            encode_frame(geometry, sequence=0)
            + encode_frame(thumbnail, sequence=1)
            + encode_frame(final, sequence=2)
        )

        decoded = tuple(decoder.feed(wire))

        assert decoded == (geometry, thumbnail, final)
        assert decoder.finish() == final

    def test_rejects_empty_success_image(self):
        with pytest.raises(MeshProtocolError, match="ready_thumbnail"):
            ThumbnailOutput(
                b"", ThumbnailStrategy.FULL, _PREVIEW_COVERAGE, None, 1, None
            )

    @pytest.mark.parametrize(
        "state", ["unknown", "pending", "", None, True, 1, [], {}], ids=repr
    )
    def test_rejects_unknown_fingerprint_state(self, state):
        decoder = FrameDecoder(
            ThumbnailRequest(
                Path("mesh"),
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )
        frame = _forge(
            FrameKind.FINAL,
            0,
            {
                "fingerprint": {
                    "state": state,
                    "failure_code": None,
                    "algorithm_version": "x",
                    "records": [],
                },
                "phase_stats": {"version": 1, "stages": []},
                "duration_ms": 1,
                "peak_rss_bytes": None,
                "coverage": encode_coverage(_PREVIEW_COVERAGE),
            },
        )

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(frame))

    @pytest.mark.parametrize(
        "missing",
        ["outcome", "geometry", "volume", "coverage", "duration_ms", "peak_rss_bytes"],
        ids=repr,
    )
    def test_rejects_missing_geometry_field(self, geometry, missing):
        raw = {
            "geometry": geometry.geometry,
            "outcome": {"state": "ready"},
            "volume": encode_volume(VolumeMeasured(1000.0)),
            "coverage": encode_coverage(_GEOMETRY_COVERAGE),
            "duration_ms": 1,
            "peak_rss_bytes": None,
        }
        del raw[missing]
        decoder = FrameDecoder(ThumbnailRequest(Path("mesh")))

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(_forge(FrameKind.GEOMETRY, 0, raw)))

    @pytest.mark.parametrize(
        "phase_stats",
        [None, {}, {"stages": []}, {"version": 2, "stages": []}],
        ids=repr,
    )
    def test_requires_versioned_phase_stats(self, phase_stats):
        decoder = FrameDecoder(
            ThumbnailRequest(
                Path("mesh"), include_geometry=False, include_thumbnail=False
            )
        )
        frame = _forge(
            FrameKind.FINAL,
            0,
            {
                "fingerprint": None,
                "phase_stats": phase_stats,
                "duration_ms": 1,
                "peak_rss_bytes": None,
                "coverage": encode_coverage(_PREVIEW_COVERAGE),
            },
        )

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(frame))

    @pytest.mark.parametrize(
        "payload", [b"MSH1", b"NOPE" + b"x" * 13, b"MSH2\x00\x00"], ids=repr
    )
    def test_rejects_obsolete_or_invalid_frame(self, payload):
        decoder = FrameDecoder(ThumbnailRequest(Path("mesh")))

        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(payload))
            decoder.finish()

    @pytest.mark.parametrize(
        "volume",
        [
            pytest.param(VolumeMeasured(1e-9), id="tiny-measured"),
            pytest.param(
                VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT), id="open"
            ),
            pytest.param(
                VolumeNotCalculated(VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED),
                id="bounded-scan",
            ),
        ],
        ids=repr,
    )
    def test_preserves_volume_evidence(self, geometry, volume):
        output = GeometryOutput(
            dict(geometry.geometry, volume_mm3=volume_value(volume)),
            GeometryReady(),
            volume,
            _GEOMETRY_COVERAGE,
            5,
            1000,
        )
        decoder = FrameDecoder(ThumbnailRequest(Path("mesh"), include_thumbnail=False))
        decoded = tuple(decoder.feed(encode_frame(output, sequence=0)))
        assert decoded == (output,)
        assert decoded[0].volume == volume
        assert decoded[0].geometry["volume_mm3"] == volume_value(volume)

    @pytest.mark.parametrize(
        "volume",
        [
            pytest.param(VolumeMeasured(2000), id="scalar-mismatch"),
            pytest.param(VolumeLegacyUnassessed(1000), id="legacy-native"),
            pytest.param(
                VolumeNotCalculated(VolumeNotCalculatedCause.ENRICHMENT_PENDING),
                id="durable-pending",
            ),
            pytest.param(
                VolumeNotCalculated(VolumeNotCalculatedCause.NOT_APPLICABLE),
                id="durable-inapplicable",
            ),
        ],
        ids=repr,
    )
    def test_rejects_conflicting_volume_evidence(self, geometry, volume):
        with pytest.raises((ValueError, TypeError)):
            GeometryOutput(
                geometry.geometry, geometry.outcome, volume, geometry.coverage, 5, 1000
            )

    @pytest.mark.parametrize(
        "cause",
        ["not_real", "", True, 1, [], {}],
        ids=["unknown", "empty", "bool", "integer", "list", "dict"],
    )
    def test_rejects_unknown_fingerprint_cause(self, cause):
        raw = {
            "fingerprint": {
                "state": "failed",
                "failure_code": cause,
                "algorithm_version": "geometry-v7-sh5f4577c4",
                "records": [],
            },
            "phase_stats": {"version": 1, "stages": []},
            "duration_ms": 1,
            "peak_rss_bytes": None,
            "coverage": encode_coverage(_GEOMETRY_COVERAGE),
        }
        decoder = FrameDecoder(
            ThumbnailRequest(
                Path("mesh"),
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )
        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(_forge(FrameKind.FINAL, 0, raw)))

    @pytest.mark.parametrize(
        "coverage",
        [
            pytest.param(_GEOMETRY_COVERAGE, id="scan-without-materialization"),
            pytest.param(
                MeshCoverage(
                    SourceScanState.COMPLETE,
                    SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
                    PreviewCoverage.NOT_PRODUCED,
                ),
                id="full-scan-sampled-geometry",
            ),
            pytest.param(
                MeshCoverage(
                    SourceScanState.NOT_SCANNED,
                    GeometryNotLoaded(),
                    PreviewCoverage.DOCUMENT_SUPPLIED,
                ),
                id="document-preview",
            ),
        ],
        ids=repr,
    )
    def test_preserves_independent_coverage(self, coverage):
        final = FingerprintFinal(None, (), 1, None, coverage)
        decoder = FrameDecoder(
            ThumbnailRequest(
                Path("mesh"), include_geometry=False, include_thumbnail=False
            )
        )
        assert tuple(decoder.feed(encode_frame(final, sequence=0))) == (final,)
        assert decoder.finish().coverage == coverage

    @pytest.mark.parametrize(
        "coverage",
        [
            None,
            {},
            {
                "source_scan": "unknown",
                "geometry": {"state": "not_loaded"},
                "preview": "not_produced",
            },
            {
                "source_scan": "not_scanned",
                "geometry": {"state": "complete"},
                "preview": "not_produced",
            },
        ],
        ids=["missing", "empty", "unknown-scan", "contradictory-full"],
    )
    def test_rejects_invalid_coverage(self, coverage):
        raw = {
            "fingerprint": None,
            "phase_stats": {"version": 1, "stages": []},
            "duration_ms": 1,
            "peak_rss_bytes": None,
            "coverage": coverage,
        }
        decoder = FrameDecoder(
            ThumbnailRequest(
                Path("mesh"), include_geometry=False, include_thumbnail=False
            )
        )
        with pytest.raises(MeshProtocolError):
            tuple(decoder.feed(_forge(FrameKind.FINAL, 0, raw)))


class TestOutputBoundaries:
    @pytest.mark.parametrize(
        "flag",
        ["include_geometry", "include_thumbnail", "include_fingerprint"],
        ids=str,
    )
    def test_rejects_nonboolean_request_flags(self, flag):
        request = ThumbnailRequest(Path("mesh"), **{flag: 1})
        with pytest.raises(MeshProtocolError, match="invalid_output_boolean"):
            FrameDecoder(request)

    @pytest.mark.parametrize(
        "chunk",
        [bytearray(b"MSH2"), "MSH2", None],
        ids=["mutable-bytes", "text", "null"],
    )
    def test_rejects_nonbytes_stream_chunks(self, chunk):
        decoder = FrameDecoder(ThumbnailRequest(Path("mesh")))
        with pytest.raises(MeshProtocolError, match="invalid_output_chunk"):
            tuple(decoder.feed(chunk))
        with pytest.raises(MeshProtocolError, match="incomplete_mesh_output"):
            decoder.finish()

    @pytest.mark.parametrize(
        "outcome,error",
        [
            (None, "invalid_output_geometry_outcome"),
            (GeometryRefused("unknown"), "invalid_output_geometry_reason"),
        ],
        ids=["unknown-outcome", "unknown-reason"],
    )
    def test_rejects_invalid_geometry_outcomes(self, geometry, outcome, error):
        with pytest.raises(MeshProtocolError, match=error):
            replace(geometry, outcome=outcome)

    @pytest.mark.parametrize("coverage", [None, {}], ids=["missing", "untyped"])
    def test_rejects_geometry_coverage_without_evidence(self, geometry, coverage):
        with pytest.raises(MeshProtocolError, match="invalid_geometry_coverage"):
            replace(geometry, coverage=coverage)

    def test_rejects_assessed_volume_on_refused_geometry(self, geometry):
        with pytest.raises(
            MeshProtocolError, match="refused_geometry_has_assessed_volume"
        ):
            replace(
                geometry,
                geometry=dict.fromkeys(geometry.geometry),
                outcome=GeometryRefused(ThumbnailFailureReason.NO_GEOMETRY),
                volume=VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT),
            )

    @pytest.mark.parametrize(
        "change,error",
        [
            ({"strategy": "full"}, "invalid_output_strategy"),
            ({"coverage": None}, "invalid_output_coverage"),
            ({"image": None}, "invalid_refused_thumbnail"),
        ],
        ids=["untyped-strategy", "missing-coverage", "incomplete-refusal"],
    )
    def test_rejects_invalid_thumbnail_contracts(self, thumbnail, change, error):
        with pytest.raises(MeshProtocolError, match=error):
            replace(thumbnail, **change)

    @pytest.mark.parametrize(
        "change,error",
        [
            ({"fingerprint": {}}, "invalid_final_fingerprint"),
            ({"phase_stats": []}, "invalid_final_phase_stats"),
            ({"coverage": None}, "invalid_output_coverage"),
        ],
        ids=["untyped-fingerprint", "untyped-telemetry", "missing-coverage"],
    )
    def test_rejects_invalid_final_contracts(self, final, change, error):
        with pytest.raises(MeshProtocolError, match=error):
            replace(final, **change)

    @pytest.mark.parametrize(
        "state",
        [FingerprintResultState.READY, FingerprintResultState.PARTIAL],
        ids=lambda s: s.value,
    )
    def test_rejects_fingerprint_coverage_mismatch(self, state_final):
        with pytest.raises(MeshProtocolError, match="fingerprint_requires"):
            replace(state_final, coverage=_GEOMETRY_COVERAGE)

    @pytest.mark.parametrize(
        "location,value",
        [
            ("algorithm_version", ""),
            ("algorithm_version", "v" * 129),
            ("algorithm_version", 1),
            ("records", {}),
            ("records", [{}] * 4098),
            ("component_index", 4097),
            ("instance_count", 0),
            ("instance_count", 2049),
            ("values", []),
            ("instances", {}),
            ("instances", [1]),
        ],
        ids=[
            "empty-version",
            "long-version",
            "untyped-version",
            "untyped-records",
            "too-many-records",
            "component-limit",
            "zero-instances",
            "instance-limit",
            "untyped-values",
            "untyped-instances",
            "invalid-instance",
        ],
    )
    def test_rejects_invalid_fingerprint_record_limits(self, final, location, value):
        raw = json.loads(encode_frame(final, sequence=0)[17:])
        fingerprint = raw["fingerprint"]
        if location in {"algorithm_version", "records"}:
            fingerprint[location] = value
        else:
            fingerprint["records"][0][location] = value
        decoder = FrameDecoder(
            ThumbnailRequest(
                Path("mesh"),
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )
        with pytest.raises(MeshProtocolError, match="invalid_mesh_output"):
            tuple(decoder.feed(_forge(FrameKind.FINAL, 0, raw)))
        with pytest.raises(MeshProtocolError, match="incomplete_mesh_output"):
            decoder.finish()

    def test_rejects_unknown_frame_type(self):
        with pytest.raises(MeshProtocolError, match="unknown_output_frame"):
            encode_frame(None, sequence=0)

    @pytest.mark.parametrize("kind", ["final-metadata", "thumbnail-payload"], ids=str)
    def test_rejects_encoded_frames_over_byte_budget(
        self, thumbnail, fingerprint, kind
    ):
        if kind == "final-metadata":
            record = FingerprintRecord(
                0, 1, {"payload": "x" * mesh_protocol.MAX_STREAM_BYTES}, ()
            )
            output = FingerprintFinal(
                replace(fingerprint, records=(record,)), (), 1, None, _PREVIEW_COVERAGE
            )
        else:
            output = replace(thumbnail, image=b"x" * mesh_protocol.MAX_STREAM_BYTES)
        with pytest.raises(MeshProtocolError, match="output_frame_too_large"):
            encode_frame(output, sequence=0)

    def test_rejects_unrequested_geometry_frame(self, geometry):
        raw = json.loads(encode_frame(geometry, sequence=0)[17:])
        raw["outcome"] = {"state": "not_requested"}
        decoder = FrameDecoder(ThumbnailRequest(Path("mesh")))
        with pytest.raises(MeshProtocolError, match="invalid_mesh_output"):
            tuple(decoder.feed(_forge(FrameKind.GEOMETRY, 0, raw)))
        with pytest.raises(MeshProtocolError, match="incomplete_mesh_output"):
            decoder.finish()

    def test_rejects_measurements_on_refused_geometry(self, geometry):
        with pytest.raises(MeshProtocolError, match="refused_output_has_geometry"):
            replace(
                geometry, outcome=GeometryRefused(ThumbnailFailureReason.NO_GEOMETRY)
            )
