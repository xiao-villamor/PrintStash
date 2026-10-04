"""Real engine stage costs retain attribution across independent output requests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import _overlay
from app.modules.media.thumbnail_engine import ThumbnailEngine, ThumbnailRequest
from tests.factories import content
from tests.factories.geometry import three_mf


@pytest.fixture
def cube(tmp_path: Path) -> Path:
    source = tmp_path / "cube.stl"
    source.write_bytes(content.binary_stl())
    return source


class TestPhaseStats:
    def test_render_reports_encoded_output_size(self, cube: Path) -> None:
        result = ThumbnailEngine().generate(
            ThumbnailRequest(cube, include_geometry=False, output_format="WEBP")
        )

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["render"].output_bytes == len(result.image)
        assert phases["render"].elapsed_ns > 0
        assert phases["render"].outcome.value == "completed"

    def test_fingerprint_only_retains_analysis_cost(self, cube: Path) -> None:
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                cube,
                include_geometry=False,
                include_thumbnail=False,
                include_fingerprint=True,
            )
        )

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["fingerprint"].elapsed_ns > 0
        assert phases["fingerprint"].outcome.value == "completed"
        assert "render" not in phases

    def test_embedded_preview_retains_extraction_cost(self, tmp_path: Path) -> None:
        source = tmp_path / "embedded.3mf"
        source.write_bytes(three_mf(extras={"Metadata/thumbnail.png": content.png()}))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(source, include_geometry=False)
        )

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["embedded"].output_bytes == len(result.image)
        assert phases["embedded"].input_bytes == source.stat().st_size
        assert "load" not in phases

    def test_refused_load_retains_failed_stage(self, tmp_path: Path) -> None:
        source = tmp_path / "broken.obj"
        source.write_bytes(b"not geometry")

        result = ThumbnailEngine().generate(ThumbnailRequest(source))

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["load"].outcome.value == "failed"
        assert phases["load"].elapsed_ns > 0
        assert phases["measurements"].triangle_count is None

    def test_streamed_preview_retains_separate_stage(
        self, cube: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(cube, include_geometry=False)
        )

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["streaming"].elapsed_ns > 0
        assert phases["streaming"].output_bytes == len(result.image)
        assert phases["streaming"].triangle_count == 12

    def test_fallback_refusal_retains_attempt_cost(self, tmp_path: Path) -> None:
        source = tmp_path / "broken.stl"
        source.write_bytes(b"not geometry")

        result = ThumbnailEngine().generate(ThumbnailRequest(source))

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["fallback"].outcome.value == "failed"
        assert phases["fallback"].elapsed_ns > 0
        assert phases["fallback"].input_bytes == source.stat().st_size
        assert phases["fallback"].output_bytes is None
