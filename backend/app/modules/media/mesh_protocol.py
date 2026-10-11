"""Closed, bounded mesh output frames independent of process supervision.

Requested measurements precede the requested thumbnail and one required final
fingerprint frame. A receiver validates whole frames before exposing outputs;
final fingerprint acceptance additionally requires the supervisor's clean exit.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable, Iterator, TypeAlias

from printstash_core.mesh.measurements import (
    VolumeLegacyUnassessed,
    VolumeMeasurement,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    decode_volume,
    encode_volume,
)

from app.modules.media.fingerprints import (
    FingerprintRecord,
    FingerprintResult,
    FingerprintResultState,
)
from app.modules.media.mesh_contracts import (
    Geometry,
    GeometryReady,
    GeometryRefused,
    MeshCoverage,
    MeshMeasurements,
    PreviewCoverage,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailStrategy,
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
from app.modules.media.mesh_telemetry import (
    PhaseStats,
    decode_phase_stats,
    encode_phase_stats,
)
from app.modules.media.mesh_wire_values import pack_value, unpack_value

FRAME_MAGIC = b"MSH2"
MAX_STREAM_BYTES = 32 * 1024 * 1024
MAX_CONTROL_BYTES = 64 * 1024
_PREFIX = struct.Struct("!4sBIII")
_GEOMETRY_FIELDS = {
    "bbox_x_mm",
    "bbox_y_mm",
    "bbox_z_mm",
    "volume_mm3",
    "triangle_count",
}
_MAX_COUNTER = 2**63 - 1


class FrameKind(IntEnum):
    GEOMETRY = 1
    THUMBNAIL = 2
    FINAL = 3


class MeshProtocolError(ValueError):
    """Untrusted worker bytes violate the requested output contract."""


def _counter(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_COUNTER:
        raise MeshProtocolError("invalid_output_counter")
    return value


def _optional_counter(value: object) -> int | None:
    return None if value is None else _counter(value)


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise MeshProtocolError("invalid_output_boolean")
    return value


def _geometry(values: object, outcome: object) -> Geometry:
    if type(values) is not dict or set(values) != _GEOMETRY_FIELDS:
        raise MeshProtocolError("invalid_output_geometry")
    if not isinstance(outcome, (GeometryReady, GeometryRefused)):
        raise MeshProtocolError("invalid_output_geometry_outcome")
    if isinstance(outcome, GeometryRefused):
        if not isinstance(outcome.reason, ThumbnailFailureReason):
            raise MeshProtocolError("invalid_output_geometry_reason")
        if any(value is not None for value in values.values()):
            raise MeshProtocolError("refused_output_has_geometry")
    else:
        count = values["triangle_count"]
        if type(count) is not int or not 1 <= count <= _MAX_COUNTER:
            raise MeshProtocolError("invalid_output_triangle_count")
        for name in _GEOMETRY_FIELDS - {"triangle_count"}:
            value = values[name]
            if value is not None and (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value < 0
                or name == "volume_mm3"
                and value == 0
            ):
                raise MeshProtocolError("invalid_output_measurement")
    return dict(values)


@dataclass(frozen=True)
class GeometryOutput:
    geometry: Geometry
    outcome: GeometryReady | GeometryRefused
    volume: VolumeMeasurement
    coverage: MeshCoverage
    duration_ms: int
    peak_rss_bytes: int | None

    def __post_init__(self) -> None:
        _geometry(self.geometry, self.outcome)
        MeshMeasurements(self.geometry, self.volume)
        if not isinstance(self.coverage, MeshCoverage):
            raise MeshProtocolError("invalid_geometry_coverage")
        if isinstance(self.volume, VolumeLegacyUnassessed) or (
            isinstance(self.volume, VolumeNotCalculated)
            and self.volume.cause
            in {
                VolumeNotCalculatedCause.ENRICHMENT_PENDING,
                VolumeNotCalculatedCause.NOT_APPLICABLE,
                VolumeNotCalculatedCause.NOT_REQUESTED,
            }
        ):
            raise MeshProtocolError("invalid_native_volume_evidence")
        if isinstance(
            self.outcome, GeometryRefused
        ) and self.volume != VolumeNotCalculated(
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
        ):
            raise MeshProtocolError("refused_geometry_has_assessed_volume")
        _counter(self.duration_ms)
        _optional_counter(self.peak_rss_bytes)


@dataclass(frozen=True)
class ThumbnailOutput:
    image: bytes | None
    strategy: ThumbnailStrategy
    coverage: MeshCoverage
    failure_reason: ThumbnailFailureReason | None
    duration_ms: int
    peak_rss_bytes: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, ThumbnailStrategy):
            raise MeshProtocolError("invalid_output_strategy")
        if not isinstance(self.coverage, MeshCoverage):
            raise MeshProtocolError("invalid_output_coverage")
        _counter(self.duration_ms)
        _optional_counter(self.peak_rss_bytes)
        if self.image is None:
            if (
                self.strategy is not ThumbnailStrategy.NONE
                or self.coverage.preview is not PreviewCoverage.NOT_PRODUCED
                or not isinstance(self.failure_reason, ThumbnailFailureReason)
            ):
                raise MeshProtocolError("invalid_refused_thumbnail")
        elif (
            type(self.image) is not bytes
            or not self.image
            or self.strategy is ThumbnailStrategy.NONE
            or self.failure_reason is not None
            or self.coverage.preview is PreviewCoverage.NOT_PRODUCED
            or (self.strategy is ThumbnailStrategy.EMBEDDED)
            != (self.coverage.preview is PreviewCoverage.DOCUMENT_SUPPLIED)
        ):
            raise MeshProtocolError("invalid_ready_thumbnail")


@dataclass(frozen=True)
class FingerprintFinal:
    fingerprint: FingerprintResult | None
    phase_stats: tuple[PhaseStats, ...]
    duration_ms: int
    peak_rss_bytes: int | None
    coverage: MeshCoverage

    def __post_init__(self) -> None:
        if self.fingerprint is not None and not isinstance(
            self.fingerprint, FingerprintResult
        ):
            raise MeshProtocolError("invalid_final_fingerprint")
        if type(self.phase_stats) is not tuple:
            raise MeshProtocolError("invalid_final_phase_stats")
        encode_phase_stats(self.phase_stats)
        _counter(self.duration_ms)
        _optional_counter(self.peak_rss_bytes)
        if not isinstance(self.coverage, MeshCoverage):
            raise MeshProtocolError("invalid_output_coverage")
        if self.fingerprint is not None:
            if (
                self.fingerprint.state is FingerprintResultState.READY
                and not isinstance(self.coverage.geometry, CompleteGeometry)
            ):
                raise MeshProtocolError("ready_fingerprint_requires_complete_geometry")
            if self.fingerprint.state is FingerprintResultState.PARTIAL and (
                not isinstance(self.coverage.geometry, SampledGeometry)
                or self.fingerprint.failure_code is not self.coverage.geometry.reason
            ):
                raise MeshProtocolError("partial_fingerprint_requires_matching_sample")


BasicOutput: TypeAlias = GeometryOutput | ThumbnailOutput
OutputFrame: TypeAlias = BasicOutput | FingerprintFinal
OutputSink: TypeAlias = Callable[[BasicOutput], None]


def _fields(raw: object, names: set[str]) -> dict[str, Any]:
    if type(raw) is not dict or set(raw) != names:
        raise MeshProtocolError("invalid_output_fields")
    return raw


def _fingerprint(raw: object) -> FingerprintResult | None:
    if raw is None:
        return None
    raw = _fields(raw, {"state", "failure_code", "algorithm_version", "records"})
    state = FingerprintResultState(raw["state"])
    code = raw["failure_code"]
    version = raw["algorithm_version"]
    if code is not None:
        if type(code) is not str:
            raise MeshProtocolError("invalid_fingerprint_failure_code")
        code = FingerprintFailureCode(code)
    if type(version) is not str or not version or len(version) > 128:
        raise MeshProtocolError("invalid_fingerprint_version")
    if type(raw["records"]) is not list or len(raw["records"]) > 4097:
        raise MeshProtocolError("invalid_fingerprint_records")
    records = []
    for record in raw["records"]:
        record = _fields(
            record, {"component_index", "instance_count", "values", "instances"}
        )
        index = _counter(record["component_index"])
        count = _counter(record["instance_count"])
        if index > 4096 or not 1 <= count <= 2048:
            raise MeshProtocolError("invalid_fingerprint_record_count")
        values = unpack_value(record["values"])
        instances = unpack_value(record["instances"])
        if (
            type(values) is not dict
            or type(instances) is not list
            or any(type(item) is not dict for item in instances)
        ):
            raise MeshProtocolError("invalid_fingerprint_record_values")
        records.append(FingerprintRecord(index, count, values, tuple(instances)))
    if state in (FingerprintResultState.READY, FingerprintResultState.PARTIAL):
        if not records or state is FingerprintResultState.READY and code is not None:
            raise MeshProtocolError("invalid_success_fingerprint")
    elif records or code is None:
        raise MeshProtocolError("invalid_failed_fingerprint")
    return FingerprintResult(state, tuple(records), code, version)


def _encode_fingerprint(result: FingerprintResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    raw = {
        "state": result.state.value,
        "failure_code": result.failure_code.value
        if result.failure_code is not None
        else None,
        "algorithm_version": result.algorithm_version,
        "records": [
            {
                "component_index": record.component_index,
                "instance_count": record.instance_count,
                "values": pack_value(record.values),
                "instances": [pack_value(item) for item in record.instances],
            }
            for record in result.records
        ],
    }
    _fingerprint(raw)
    return raw


def encode_frame(frame: OutputFrame, *, sequence: int) -> bytes:
    _counter(sequence)
    if sequence > 2:
        raise MeshProtocolError("invalid_output_sequence")
    image = b""
    if isinstance(frame, GeometryOutput):
        kind = FrameKind.GEOMETRY
        header = {
            "geometry": frame.geometry,
            "outcome": encode_geometry(frame.outcome),
            "volume": encode_volume(frame.volume),
            "coverage": encode_coverage(frame.coverage),
            "duration_ms": frame.duration_ms,
            "peak_rss_bytes": frame.peak_rss_bytes,
        }
    elif isinstance(frame, ThumbnailOutput):
        kind = FrameKind.THUMBNAIL
        image = frame.image if frame.image is not None else b""
        header = {
            "strategy": frame.strategy.value,
            "coverage": encode_coverage(frame.coverage),
            "failure_reason": frame.failure_reason.value
            if frame.failure_reason is not None
            else None,
            "has_image": frame.image is not None,
            "image_length": len(image),
            "duration_ms": frame.duration_ms,
            "peak_rss_bytes": frame.peak_rss_bytes,
        }
    elif isinstance(frame, FingerprintFinal):
        kind = FrameKind.FINAL
        header = {
            "fingerprint": _encode_fingerprint(frame.fingerprint),
            "phase_stats": encode_phase_stats(frame.phase_stats),
            "duration_ms": frame.duration_ms,
            "peak_rss_bytes": frame.peak_rss_bytes,
            "coverage": encode_coverage(frame.coverage),
        }
    else:
        raise MeshProtocolError("unknown_output_frame")
    body = json.dumps(header, allow_nan=False, separators=(",", ":")).encode()
    limit = MAX_STREAM_BYTES if kind is FrameKind.FINAL else MAX_CONTROL_BYTES
    if len(body) > limit or _PREFIX.size + len(body) + len(image) > MAX_STREAM_BYTES:
        raise MeshProtocolError("output_frame_too_large")
    return (
        _PREFIX.pack(FRAME_MAGIC, kind, sequence, len(body), len(image)) + body + image
    )


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in items:
        if key in result:
            raise MeshProtocolError("duplicate_output_field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise MeshProtocolError("nonfinite_output_json")


def _decode(kind: FrameKind, payload: bytes, image: bytes) -> OutputFrame:
    raw = json.loads(
        payload,
        object_pairs_hook=_pairs,
        parse_constant=_reject_constant,
    )
    if kind is FrameKind.GEOMETRY:
        raw = _fields(
            raw,
            {
                "geometry",
                "outcome",
                "volume",
                "coverage",
                "duration_ms",
                "peak_rss_bytes",
            },
        )
        outcome = decode_geometry(raw["outcome"])
        if not isinstance(outcome, GeometryReady | GeometryRefused):
            raise MeshProtocolError("unrequested_geometry_output")
        return GeometryOutput(
            _geometry(raw["geometry"], outcome),
            outcome,
            decode_volume(raw["volume"]),
            decode_coverage(raw["coverage"]),
            raw["duration_ms"],
            raw["peak_rss_bytes"],
        )
    if kind is FrameKind.THUMBNAIL:
        raw = _fields(
            raw,
            {
                "strategy",
                "coverage",
                "failure_reason",
                "has_image",
                "image_length",
                "duration_ms",
                "peak_rss_bytes",
            },
        )
        has_image = _boolean(raw["has_image"])
        if _counter(raw["image_length"]) != len(image) or not has_image and image:
            raise MeshProtocolError("invalid_output_image_length")
        return ThumbnailOutput(
            image if has_image else None,
            ThumbnailStrategy(raw["strategy"]),
            decode_coverage(raw["coverage"]),
            None
            if raw["failure_reason"] is None
            else ThumbnailFailureReason(raw["failure_reason"]),
            raw["duration_ms"],
            raw["peak_rss_bytes"],
        )
    raw = _fields(
        raw, {"fingerprint", "phase_stats", "duration_ms", "peak_rss_bytes", "coverage"}
    )
    return FingerprintFinal(
        _fingerprint(raw["fingerprint"]),
        decode_phase_stats(raw["phase_stats"]),
        raw["duration_ms"],
        raw["peak_rss_bytes"],
        decode_coverage(raw["coverage"]),
    )


class FrameDecoder:
    """Incrementally decode only the requested sequence, under one byte ceiling."""

    def __init__(self, request: ThumbnailRequest) -> None:
        _boolean(request.include_geometry)
        _boolean(request.include_thumbnail)
        _boolean(request.include_fingerprint)
        self._expected = (
            *((FrameKind.GEOMETRY,) if request.include_geometry else ()),
            *((FrameKind.THUMBNAIL,) if request.include_thumbnail else ()),
            FrameKind.FINAL,
        )
        self._fingerprint_requested = request.include_fingerprint
        self._buffer = bytearray()
        self._received = 0
        self._sequence = 0
        self._final: FingerprintFinal | None = None

    def feed(self, chunk: bytes) -> Iterator[OutputFrame]:
        if type(chunk) is not bytes:
            raise MeshProtocolError("invalid_output_chunk")
        self._received += len(chunk)
        if self._received > MAX_STREAM_BYTES or self._final is not None and chunk:
            raise MeshProtocolError("invalid_output_stream_size")
        self._buffer.extend(chunk)
        try:
            while len(self._buffer) >= _PREFIX.size:
                magic, tag, sequence, header_size, image_size = _PREFIX.unpack_from(
                    self._buffer
                )
                kind = FrameKind(tag)
                if (
                    magic != FRAME_MAGIC
                    or sequence != self._sequence
                    or self._sequence >= len(self._expected)
                    or kind is not self._expected[self._sequence]
                ):
                    raise MeshProtocolError("invalid_output_order")
                limit = (
                    MAX_STREAM_BYTES if kind is FrameKind.FINAL else MAX_CONTROL_BYTES
                )
                size = _PREFIX.size + header_size + image_size
                if (
                    not header_size
                    or header_size > limit
                    or size > MAX_STREAM_BYTES
                    or kind is not FrameKind.THUMBNAIL
                    and image_size
                ):
                    raise MeshProtocolError("invalid_output_length")
                if len(self._buffer) < size:
                    break
                frame = _decode(
                    kind,
                    bytes(self._buffer[_PREFIX.size : _PREFIX.size + header_size]),
                    bytes(self._buffer[_PREFIX.size + header_size : size]),
                )
                del self._buffer[:size]
                self._sequence += 1
                if isinstance(frame, FingerprintFinal):
                    if (
                        frame.fingerprint is not None
                    ) is not self._fingerprint_requested or self._buffer:
                        raise MeshProtocolError("invalid_terminal_output")
                    self._final = frame
                yield frame
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OverflowError,
            struct.error,
            RecursionError,
        ) as exc:
            raise MeshProtocolError("invalid_mesh_output") from exc

    def finish(self) -> FingerprintFinal:
        if self._final is None or self._buffer or self._sequence != len(self._expected):
            raise MeshProtocolError("incomplete_mesh_output")
        return self._final
