"""Real engine previews and stage costs preserve their geometry contracts."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

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


class TestThumbnailEngine:
    def test_preserves_translated_3mf_preview(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        origin = tmp_path / "origin.3mf"
        translated = tmp_path / "translated.3mf"
        origin.write_bytes(three_mf(meshes={1: mesh}))
        translated.write_bytes(
            three_mf(
                meshes={1: mesh},
                build=((1, "1 0 0 0 1 0 0 0 1 1000000000 1000000000 1000000000"),),
            )
        )

        original = ThumbnailEngine().generate(
            ThumbnailRequest(origin, width=128, height=128)
        )
        shifted = ThumbnailEngine().generate(
            ThumbnailRequest(translated, width=128, height=128)
        )

        assert original.image is not None and shifted.image is not None
        original_pixels = np.asarray(Image.open(io.BytesIO(original.image)))
        shifted_pixels = np.asarray(Image.open(io.BytesIO(shifted.image)))
        assert np.count_nonzero(original_pixels[..., 3]) > 0
        np.testing.assert_array_equal(shifted_pixels, original_pixels)

    def test_retains_translated_3mf_metadata(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        path = tmp_path / "translated.3mf"
        path.write_bytes(
            three_mf(
                meshes={1: mesh},
                build=((1, "1 0 0 0 1 0 0 0 1 1000000000 1000000000 1000000000"),),
            )
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path, width=64, height=64))

        assert result.geometry == pytest.approx(
            {
                "bbox_x_mm": 10,
                "bbox_y_mm": 10,
                "bbox_z_mm": 10,
                "volume_mm3": 1000,
                "triangle_count": 12,
            }
        )
