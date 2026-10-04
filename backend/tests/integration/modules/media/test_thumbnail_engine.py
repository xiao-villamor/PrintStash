"""Large physical placements keep usable previews without changing mesh measurements."""

import io

import numpy as np
import pytest
import trimesh
from PIL import Image

from app.modules.media.thumbnail_engine import ThumbnailEngine, ThumbnailRequest
from tests.factories.geometry import three_mf


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
