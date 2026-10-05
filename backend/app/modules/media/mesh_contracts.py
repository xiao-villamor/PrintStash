"""Stable mesh derivative data and tagged geometry outcomes.

Workers, supervisors and publishers share these contracts without importing the
thumbnail strategy owner. Geometry tags preserve refusal independently from a
usable preview; transport framing remains with the supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Literal, Optional, Protocol

from printstash_core.mesh.measurements import (
    VolumeLegacyUnassessed,
    VolumeMeasurement,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    validate_geometry_extents,
    volume_value,
)
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

from app.modules.media.mesh_facts import (
    CompleteGeometry,
    FingerprintFailureCode,
    FingerprintResultState,
    SampledGeometry,
)
from app.modules.media.mesh_telemetry import PhaseStats, SupervisionStats

if TYPE_CHECKING:
    from app.modules.media.fingerprints import FingerprintResult

Geometry = Dict[str, Optional[float]]
ProgressReporter = Callable[[str], None]


class ThumbnailStrategy(str, Enum):
    NONE = "none"
    EMBEDDED = "embedded"
    FULL = "full"
    STREAMING = "streaming"
    FALLBACK = "fallback"


class ThumbnailFailureReason(str, Enum):
    INVALID_SOURCE = "invalid_source"
    SOURCE_CHANGED = "source_changed"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    NO_GEOMETRY = "no_geometry"
    RESOURCE_LIMIT = "resource_limit"
    TIMEOUT = "timeout"
    RENDERER_NO_OUTPUT = "renderer_no_output"
    WORKER_FAILED = "worker_failed"
    STORAGE = "storage"


@dataclass(frozen=True)
class GeometryReady:
    """Measurements were obtained; individual unknown measurements are legitimate."""


@dataclass(frozen=True)
class GeometryRefused:
    reason: ThumbnailFailureReason


@dataclass(frozen=True)
class GeometryNotRequested:
    pass


GeometryOutcome = GeometryReady | GeometryRefused | GeometryNotRequested


class SourceScanState(str, Enum):
    NOT_SCANNED = "not_scanned"
    COMPLETE = "complete"
    PARTIAL = "partial"


class PreviewCoverage(str, Enum):
    NOT_PRODUCED = "not_produced"
    DOCUMENT_SUPPLIED = "document_supplied"
    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True)
class GeometryNotLoaded:
    """No materialized geometry was needed or obtained."""


GeometryRepresentation = GeometryNotLoaded | CompleteGeometry | SampledGeometry


@dataclass(frozen=True)
class MeshCoverage:
    """Independent facts about source reading, materialization and the image."""

    source_scan: SourceScanState
    geometry: GeometryRepresentation
    preview: PreviewCoverage

    def __post_init__(self) -> None:
        if not isinstance(self.source_scan, SourceScanState):
            raise TypeError("invalid source scan state")
        if not isinstance(self.preview, PreviewCoverage):
            raise TypeError("invalid preview coverage")
        if not isinstance(
            self.geometry, GeometryNotLoaded | CompleteGeometry | SampledGeometry
        ):
            raise TypeError("invalid geometry representation")
        if (
            isinstance(self.geometry, CompleteGeometry)
            and self.source_scan is not SourceScanState.COMPLETE
        ):
            raise ValueError("complete geometry requires a complete source scan")
        if (
            isinstance(self.geometry, SampledGeometry)
            and self.source_scan is SourceScanState.NOT_SCANNED
        ):
            raise ValueError("sampled geometry requires a source scan")


@dataclass(frozen=True)
class MeshMeasurements:
    geometry: Geometry
    volume: VolumeMeasurement

    @classmethod
    def unavailable(cls) -> MeshMeasurements:
        """Unknown geometry with explicit evidence that it was not obtained."""
        return cls(
            {
                "bbox_x_mm": None,
                "bbox_y_mm": None,
                "bbox_z_mm": None,
                "volume_mm3": None,
                "triangle_count": None,
            },
            VolumeNotCalculated(VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE),
        )

    def __post_init__(self) -> None:
        validate_geometry_extents(self.geometry)
        scalar = self.geometry["volume_mm3"]
        if isinstance(scalar, bool) or scalar != volume_value(self.volume):
            raise ValueError("volume scalar disagrees with measurement evidence")


@dataclass(frozen=True)
class ThumbnailRequest:
    path: Path
    file_type: str | None = None
    width: int | None = None
    height: int | None = None
    include_geometry: bool = True
    reason: str = "ingestion"
    report: ProgressReporter | None = None
    output_format: Literal["PNG", "WEBP"] = "PNG"
    include_fingerprint: bool = False
    triangle_cap: int = MAX_ANALYSIS_FACES
    include_thumbnail: bool = True


@dataclass(frozen=True)
class ThumbnailResult:
    image: bytes | None
    geometry: Geometry
    geometry_outcome: GeometryOutcome
    volume: VolumeMeasurement
    strategy: ThumbnailStrategy
    coverage: MeshCoverage
    failure_reason: ThumbnailFailureReason | None
    duration_ms: int
    peak_rss_bytes: int | None
    fingerprint_result: FingerprintResult | None = None
    phase_stats: tuple[PhaseStats, ...] = ()
    supervision: SupervisionStats | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.coverage, MeshCoverage):
            raise TypeError("invalid mesh coverage")
        if (self.image is None) != (
            self.coverage.preview is PreviewCoverage.NOT_PRODUCED
        ):
            raise ValueError("preview coverage disagrees with image presence")
        if (
            self.strategy is ThumbnailStrategy.EMBEDDED
            and self.coverage.preview is not PreviewCoverage.DOCUMENT_SUPPLIED
        ):
            raise ValueError("embedded preview requires document supplied coverage")
        if (
            self.coverage.preview is PreviewCoverage.DOCUMENT_SUPPLIED
            and self.strategy is not ThumbnailStrategy.EMBEDDED
        ):
            raise ValueError("document supplied preview requires embedded strategy")
        if isinstance(
            self.geometry_outcome, GeometryNotRequested
        ) and self.volume != VolumeNotCalculated(
            VolumeNotCalculatedCause.NOT_REQUESTED
        ):
            raise ValueError("volume was not requested")
        if isinstance(
            self.geometry_outcome, GeometryRefused
        ) and self.volume != VolumeNotCalculated(
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
        ):
            raise ValueError("refused geometry cannot carry assessed volume")
        if isinstance(self.volume, VolumeNotCalculated) and self.volume.cause in (
            VolumeNotCalculatedCause.ENRICHMENT_PENDING,
            VolumeNotCalculatedCause.NOT_APPLICABLE,
        ):
            raise ValueError("native output cannot carry durable row volume causes")
        if isinstance(self.volume, VolumeLegacyUnassessed):
            raise ValueError("native output cannot be legacy unassessed")
        if self.fingerprint_result is not None:
            if (
                self.fingerprint_result.state is FingerprintResultState.READY
                and not isinstance(self.coverage.geometry, CompleteGeometry)
            ):
                raise ValueError("ready fingerprint requires complete geometry")
            if self.fingerprint_result.state is FingerprintResultState.PARTIAL:
                if not isinstance(self.coverage.geometry, SampledGeometry):
                    raise ValueError("partial fingerprint requires sampled geometry")
                if (
                    self.fingerprint_result.failure_code
                    is not self.coverage.geometry.reason
                ):
                    raise ValueError(
                        "partial fingerprint cause disagrees with sampled geometry"
                    )
        MeshMeasurements(self.geometry, self.volume)


class ThumbnailMetricsSink(Protocol):
    def increment(self, name: str, *, labels: dict[str, str]) -> None: ...

    def observe(self, name: str, value: float, *, labels: dict[str, str]) -> None: ...


def encode_geometry(outcome: GeometryOutcome) -> dict[str, str]:
    if isinstance(outcome, GeometryReady):
        return {"state": "ready"}
    if isinstance(outcome, GeometryRefused):
        return {"state": "refused", "reason": outcome.reason.value}
    if isinstance(outcome, GeometryNotRequested):
        return {"state": "not_requested"}
    raise TypeError("invalid geometry outcome")


def decode_geometry(raw: dict[str, str]) -> GeometryOutcome:
    if raw == {"state": "ready"}:
        return GeometryReady()
    if raw == {"state": "not_requested"}:
        return GeometryNotRequested()
    if set(raw) == {"state", "reason"} and raw["state"] == "refused":
        return GeometryRefused(ThumbnailFailureReason(raw["reason"]))
    raise ValueError("invalid geometry outcome")


def encode_coverage(coverage: MeshCoverage) -> dict[str, object]:
    geometry: dict[str, str]
    if isinstance(coverage.geometry, GeometryNotLoaded):
        geometry = {"state": "not_loaded"}
    elif isinstance(coverage.geometry, CompleteGeometry):
        geometry = {"state": "complete"}
    elif isinstance(coverage.geometry, SampledGeometry):
        geometry = {"state": "sampled", "reason": coverage.geometry.reason.value}
    else:
        raise TypeError("invalid geometry representation")
    return {
        "source_scan": coverage.source_scan.value,
        "geometry": geometry,
        "preview": coverage.preview.value,
    }


def decode_coverage(raw: object) -> MeshCoverage:
    if not isinstance(raw, dict) or set(raw) != {"source_scan", "geometry", "preview"}:
        raise ValueError("invalid mesh coverage")
    geometry = raw["geometry"]
    representation: GeometryRepresentation
    if geometry == {"state": "not_loaded"}:
        representation = GeometryNotLoaded()
    elif geometry == {"state": "complete"}:
        representation = CompleteGeometry()
    elif (
        isinstance(geometry, dict)
        and set(geometry) == {"state", "reason"}
        and geometry["state"] == "sampled"
    ):
        representation = SampledGeometry(FingerprintFailureCode(geometry["reason"]))
    else:
        raise ValueError("invalid geometry coverage")
    return MeshCoverage(
        source_scan=SourceScanState(raw["source_scan"]),
        geometry=representation,
        preview=PreviewCoverage(raw["preview"]),
    )
