"""A benchmark retains unexpected failures instead of counting them as speedups."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import trimesh
from printstash_core.mesh.measurements import (
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
)

from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryNotRequested,
    MeshCoverage,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.media.mesh_telemetry import MeshPhase, PhaseOutcome, PhaseStats
from app.modules.media.thumbnail_engine import ThumbnailEngine
from app.modules.storage.storage_backend.local import LocalStorageBackend
from scripts import bench_thumbnails
from scripts.bench_thumbnails import benchmark_file
from tests.factories import content


@pytest.fixture
def cube(tmp_path: Path) -> Path:
    source = tmp_path / "cube.stl"
    source.write_bytes(trimesh.creation.box().export(file_type="stl"))
    return source


@pytest.fixture
def rendered_result() -> ThumbnailResult:
    return ThumbnailResult(
        image=content.png(),
        geometry={"volume_mm3": None},
        geometry_outcome=GeometryNotRequested(),
        volume=VolumeNotCalculated(VolumeNotCalculatedCause.NOT_REQUESTED),
        strategy=ThumbnailStrategy.FULL,
        coverage=MeshCoverage(
            SourceScanState.COMPLETE, GeometryNotLoaded(), PreviewCoverage.COMPLETE
        ),
        failure_reason=None,
        duration_ms=1,
        peak_rss_bytes=None,
        phase_stats=(
            PhaseStats(MeshPhase.LOAD, 123, 684, None, 12, PhaseOutcome.COMPLETED),
            PhaseStats(MeshPhase.RENDER, 456, None, 37, 12, PhaseOutcome.COMPLETED),
        ),
    )


class TestBenchmarkFile:
    def test_retains_returned_stage_statistics(
        self,
        cube: Path,
        rendered_result: ThumbnailResult,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            ThumbnailEngine, "generate", lambda self, request: rendered_result
        )

        result = benchmark_file(cube, cold_runs=1, warm_runs=1)

        assert result.renders[0].phase_stats == rendered_result.phase_stats
        assert result.renders[0].error is None

    def test_retains_refusal_stage_statistics(
        self,
        cube: Path,
        rendered_result: ThumbnailResult,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refused = replace(
            rendered_result,
            image=None,
            coverage=MeshCoverage(
                SourceScanState.NOT_SCANNED, GeometryNotLoaded(), PreviewCoverage.NOT_PRODUCED
            ),
            failure_reason=ThumbnailFailureReason.INVALID_SOURCE,
            strategy=ThumbnailStrategy.NONE,
            phase_stats=(
                PhaseStats(MeshPhase.LOAD, 321, 684, None, None, PhaseOutcome.FAILED),
            ),
        )
        monkeypatch.setattr(ThumbnailEngine, "generate", lambda self, request: refused)

        result = benchmark_file(cube, cold_runs=1, warm_runs=1)

        assert result.renders[0].phase_stats == refused.phase_stats
        assert result.renders[0].error == "invalid_source"
        assert result.representation_reads == []

    def test_retains_stages_after_encoding_failure(
        self,
        cube: Path,
        rendered_result: ThumbnailResult,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            ThumbnailEngine, "generate", lambda self, request: rendered_result
        )

        def fail(data: bytes) -> bytes:
            raise ValueError("encoding failed")

        monkeypatch.setattr(bench_thumbnails, "to_webp", fail)

        result = benchmark_file(cube, cold_runs=1, warm_runs=1)

        assert result.renders[0].phase_stats == rendered_result.phase_stats
        assert result.renders[0].error == "ValueError: encoding failed"
        assert result.representation_reads == []

    def test_retains_unexpected_engine_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "model.stl"
        source.write_bytes(b"source")

        def fail(self: ThumbnailEngine, request: ThumbnailRequest) -> ThumbnailResult:
            raise AttributeError("engine contract regressed")

        monkeypatch.setattr(ThumbnailEngine, "generate", fail)

        result = benchmark_file(source, cold_runs=2, warm_runs=1)

        assert [sample.error for sample in result.renders] == [
            "AttributeError: engine contract regressed",
            "AttributeError: engine contract regressed",
        ]
        assert result.renders[0].elapsed_ms > 0
        assert result.renders[0].peak_rss_bytes > 0
        assert result.renders[0].phase_stats == ()
        assert result.render_median_ms is None
        assert result.representation_reads == []

    def test_retains_publication_failure(
        self, cube: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(self: LocalStorageBackend, data: bytes, key: str) -> int:
            raise OSError("storage full")

        monkeypatch.setattr(LocalStorageBackend, "write_bytes", fail)

        result = benchmark_file(cube, cold_runs=1, warm_runs=1)

        assert result.publication_error == "OSError: storage full"
        assert result.representation_reads == []
        assert result.representation_read_median_ms is None

    def test_retains_representation_read_failure(
        self, cube: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(self: LocalStorageBackend, key: str):
            raise OSError("storage read failed")

        monkeypatch.setattr(LocalStorageBackend, "object_info", fail)

        result = benchmark_file(cube, cold_runs=1, warm_runs=2)

        assert [sample.error for sample in result.representation_reads] == [
            "OSError: storage read failed",
            "OSError: storage read failed",
        ]
        assert result.representation_reads[0].elapsed_ms > 0
        assert result.representation_read_median_ms is None

    def test_excludes_failed_attempt_from_successful_median(
        self, cube: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        generate = ThumbnailEngine.generate
        attempts = iter([False, True])

        def fail_once(
            self: ThumbnailEngine, request: ThumbnailRequest
        ) -> ThumbnailResult:
            if not next(attempts):
                raise RuntimeError("first attempt failed")
            return generate(self, request)

        monkeypatch.setattr(ThumbnailEngine, "generate", fail_once)

        result = benchmark_file(cube, cold_runs=2, warm_runs=1)

        assert result.renders[0].error == "RuntimeError: first attempt failed"
        assert result.render_median_ms == result.renders[1].elapsed_ms
        assert result.renders[1].error is None

    @pytest.mark.parametrize(
        ("cold_runs", "warm_runs"),
        [
            pytest.param(0, 1, id="zero-render"),
            pytest.param(1, 0, id="zero-read"),
            pytest.param(-1, 1, id="negative-render"),
            pytest.param(1, -1, id="negative-read"),
        ],
    )
    def test_rejects_nonpositive_repetitions(
        self, tmp_path: Path, cold_runs: int, warm_runs: int
    ) -> None:
        with pytest.raises(ValueError, match="run counts must be positive"):
            benchmark_file(
                tmp_path / "unused.stl", cold_runs=cold_runs, warm_runs=warm_runs
            )
