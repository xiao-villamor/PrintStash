"""Measure the real native supervisor, disposable child and decoded result."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.modules.media.mesh_contracts import (
    GeometryRefused,
    PreviewCoverage,
    ThumbnailRequest,
    ThumbnailStrategy,
)
from app.modules.media.mesh_isolation import MeshWorkerError, generate
from app.modules.media.mesh_telemetry import PhaseStats, SupervisionStats
from scripts.benchmark_pipeline_contracts import (
    InputIdentity,
    SampleOutcome,
    native_failure_outcome,
)


@dataclass(frozen=True)
class NativeObservation:
    name: str
    input_sha256: str
    input_bytes: int
    sample_index: int
    elapsed_ms: float
    outcome: SampleOutcome
    reason: str | None
    output_bytes: int
    output_sha256: str | None
    strategy: ThumbnailStrategy | None
    complete: bool
    phase_stats: tuple[PhaseStats, ...]
    supervision: SupervisionStats | None
    child_engine_elapsed_ms: int | None
    child_peak_rss_bytes: int | None
    output_format: str | None
    output_dimensions: tuple[int, int] | None


def measure_worker(path: Path, identity: InputIdentity) -> NativeObservation:
    started = time.perf_counter()
    phase_stats: tuple[PhaseStats, ...] = ()
    supervision = None
    output = None
    strategy = None
    complete = False
    reason = None
    child_elapsed = child_rss = None
    output_format = output_dimensions = None
    try:
        result = generate(
            ThumbnailRequest(
                path=path,
                include_geometry=True,
                include_thumbnail=True,
                include_fingerprint=False,
                output_format="WEBP",
                reason="benchmark",
            )
        )
        phase_stats = result.phase_stats
        supervision = result.supervision
        output = result.image
        strategy = result.strategy
        complete = result.coverage.preview in (
            PreviewCoverage.COMPLETE,
            PreviewCoverage.DOCUMENT_SUPPLIED,
        )
        child_elapsed = result.duration_ms
        child_rss = result.peak_rss_bytes
        if output is not None:
            with Image.open(BytesIO(output)) as image:
                output_format = image.format
                output_dimensions = image.size
        if output is None or isinstance(result.geometry_outcome, GeometryRefused):
            failure_reason = (
                result.geometry_outcome.reason
                if isinstance(result.geometry_outcome, GeometryRefused)
                else result.failure_reason
            )
            if failure_reason is None:
                raise RuntimeError("missing geometry/thumbnail refusal cause")
            reason = failure_reason.value
            outcome = native_failure_outcome(failure_reason)
        else:
            outcome = SampleOutcome.COMPLETED
    except MeshWorkerError as exc:
        supervision = exc.supervision
        reason = exc.reason.value
        outcome = native_failure_outcome(exc.reason)
    except Exception as exc:
        # Failed benchmark attempts are data; the corpus must continue after an
        # unexpected engine error rather than silently dropping that sample.
        outcome = SampleOutcome.FAILED
        reason = f"{type(exc).__name__}: {exc}"
    return NativeObservation(
        name=identity.name,
        input_sha256=identity.input_sha256,
        input_bytes=identity.input_bytes,
        sample_index=identity.sample_index,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        outcome=outcome,
        reason=reason,
        output_bytes=len(output) if output is not None else 0,
        output_sha256=hashlib.sha256(output).hexdigest()
        if output is not None
        else None,
        strategy=strategy,
        complete=complete,
        phase_stats=phase_stats,
        supervision=supervision,
        child_engine_elapsed_ms=child_elapsed,
        child_peak_rss_bytes=child_rss,
        output_format=output_format,
        output_dimensions=output_dimensions,
    )
