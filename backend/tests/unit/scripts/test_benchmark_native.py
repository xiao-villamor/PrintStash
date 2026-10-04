"""Native benchmark evidence must survive partial results and process failures."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from printstash_core.mesh.measurements import (
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
)

from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryReady,
    GeometryRefused,
    MeshCoverage,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.mesh_telemetry import SupervisionStats, WorkerExitCause
from scripts import benchmark_native
from scripts.benchmark_pipeline_contracts import InputIdentity, SampleOutcome
from tests.factories import content


@pytest.fixture
def native_result() -> ThumbnailResult:
    return ThumbnailResult(
        image=content.png(),
        geometry={"volume_mm3": None},
        geometry_outcome=GeometryReady(),
        volume=VolumeNotCalculated(VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED),
        strategy=ThumbnailStrategy.FULL,
        coverage=MeshCoverage(
            SourceScanState.COMPLETE, GeometryNotLoaded(), PreviewCoverage.COMPLETE
        ),
        failure_reason=None,
        duration_ms=42,
        peak_rss_bytes=2**20,
    )


@pytest.fixture
def input_identity() -> InputIdentity:
    return InputIdentity("cube.stl", "a" * 64, 684, 1)


class TestMeasureWorker:
    @pytest.mark.parametrize(
        "reason",
        [
            pytest.param(
                ThumbnailFailureReason.WORKER_FAILED,
                id="worker_crash",
            ),
            pytest.param(ThumbnailFailureReason.STORAGE, id="storage_error"),
            pytest.param(
                ThumbnailFailureReason.RENDERER_NO_OUTPUT,
                id="renderer_error",
            ),
        ],
    )
    def test_classifies_native_execution_failure(
        self, monkeypatch, input_identity, reason
    ):
        evidence = SupervisionStats(
            "c" * 32, 1_000_000, 100_000, 0, WorkerExitCause.EXITED_NONZERO
        )
        error = MeshWorkerError(reason)
        error.supervision = evidence

        def fail(request):
            raise error

        monkeypatch.setattr(benchmark_native, "generate", fail)
        result = benchmark_native.measure_worker(Path("cube.stl"), input_identity)
        assert result.outcome == SampleOutcome.FAILED
        assert result.reason == reason.value
        assert result.supervision == evidence

    @pytest.mark.parametrize(
        "reason",
        [ThumbnailFailureReason.INVALID_SOURCE, ThumbnailFailureReason.RESOURCE_LIMIT],
    )
    def test_preserves_native_policy_refusal(self, monkeypatch, input_identity, reason):
        error = MeshWorkerError(reason)

        def fail(request):
            raise error

        monkeypatch.setattr(benchmark_native, "generate", fail)
        result = benchmark_native.measure_worker(Path("cube.stl"), input_identity)
        assert result.outcome == SampleOutcome.REFUSED
        assert result.reason == reason.value
        assert result.supervision is None

    def test_rejects_missing_refusal_cause(
        self, monkeypatch, native_result, input_identity
    ):
        invalid = replace(
            native_result,
            image=None,
            coverage=MeshCoverage(
                SourceScanState.COMPLETE,
                GeometryNotLoaded(),
                PreviewCoverage.NOT_PRODUCED,
            ),
        )
        monkeypatch.setattr(benchmark_native, "generate", lambda request: invalid)
        result = benchmark_native.measure_worker(Path("cube.stl"), input_identity)
        assert result.outcome == SampleOutcome.FAILED
        assert result.reason == "RuntimeError: missing geometry/thumbnail refusal cause"
        assert result.child_engine_elapsed_ms == native_result.duration_ms

    def test_preserves_child_engine_evidence(
        self, monkeypatch, native_result, input_identity
    ):
        monkeypatch.setattr(benchmark_native, "generate", lambda request: native_result)
        result = benchmark_native.measure_worker(Path("cube.stl"), input_identity)
        assert result.child_engine_elapsed_ms == 42
        assert result.child_peak_rss_bytes == 2**20
        assert result.outcome == SampleOutcome.COMPLETED

    def test_reports_observed_output_format(
        self, monkeypatch, native_result, input_identity
    ):
        monkeypatch.setattr(benchmark_native, "generate", lambda request: native_result)
        result = benchmark_native.measure_worker(Path("cube.stl"), input_identity)
        assert result.output_format == "PNG"
        assert result.output_dimensions == (8, 8)
        assert result.output_bytes == len(native_result.image)

    def test_preserves_geometry_refusal_with_preview(
        self, monkeypatch, native_result, input_identity
    ):
        refused = replace(
            native_result,
            geometry_outcome=GeometryRefused(ThumbnailFailureReason.RESOURCE_LIMIT),
            volume=VolumeNotCalculated(VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE),
        )
        monkeypatch.setattr(benchmark_native, "generate", lambda request: refused)
        result = benchmark_native.measure_worker(Path("cube.stl"), input_identity)
        assert result.outcome == SampleOutcome.REFUSED
        assert result.reason == ThumbnailFailureReason.RESOURCE_LIMIT.value
        assert result.output_bytes > 0

    def test_retains_unexpected_worker_failure(self, monkeypatch, input_identity):
        def fail(request):
            raise RuntimeError("worker boundary failed")

        monkeypatch.setattr(benchmark_native, "generate", fail)
        result = benchmark_native.measure_worker(Path("cube.stl"), input_identity)
        assert result.outcome == SampleOutcome.FAILED
        assert result.reason == "RuntimeError: worker boundary failed"
        assert result.elapsed_ms >= 0
        assert result.output_sha256 is None
        assert result.phase_stats == ()
        assert result.child_engine_elapsed_ms is None

    def test_preserves_native_deadline_cost(self, monkeypatch, input_identity):
        evidence = SupervisionStats(
            "b" * 32, 1_000_000, 100_000, 0, WorkerExitCause.DEADLINE
        )
        error = MeshWorkerError(ThumbnailFailureReason.TIMEOUT)
        error.supervision = evidence

        def fail(request):
            raise error

        monkeypatch.setattr(benchmark_native, "generate", fail)
        result = benchmark_native.measure_worker(Path("cube.stl"), input_identity)
        assert result.outcome == SampleOutcome.TIMEOUT
        assert result.supervision == evidence
        assert result.child_engine_elapsed_ms is None
        assert result.phase_stats == ()
