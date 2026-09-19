"""Budgeted native depth matches the reference's partial tiles and float32 commits."""

import numpy as np
import printstash_mesh_native as native
import pytest
from reference_rasterizer import RasterBudget, _rasterise_triangles


class TestStreamingDepth:
    def test_rejects_an_excessive_face_batch(self):
        with pytest.raises(ValueError, match="buffers"):
            native.streaming_depth(bytes(2_000_001 * 36), bytes(16), 2, 2, 1)

    @pytest.mark.parametrize(
        "remaining",
        [0, 1, 19, 250_001, 1_000_000],
        ids=["empty", "pixel", "partial", "split", "full"],
    )
    def test_preserves_budgeted_depth(self, remaining):
        rng = np.random.default_rng(42)
        triangles = rng.uniform(-40, 600, (12, 3, 3)).astype(np.float32)
        triangles[:, :, 2] *= 1e-3
        image = np.zeros((512, 512, 3), np.uint8)
        depth = np.full((512, 512), np.inf, np.float32)
        budget = RasterBudget(limit=remaining)
        packed, used = native.streaming_depth(
            triangles.tobytes(), depth.tobytes(), 512, 512, remaining
        )

        _rasterise_triangles(
            image,
            depth,
            triangles,
            np.ones_like(triangles),
            lambda n: np.ones_like(n),
            np.ones(3),
            512,
            512,
            budget=budget,
        )

        assert used == budget.used
        np.testing.assert_array_equal(
            np.frombuffer(packed, np.float32).reshape(512, 512), depth
        )

    def test_preserves_existing_occlusion(self):
        triangles = np.array([[[0, 0, 1], [4, 0, 1], [0, 4, 1]]], np.float32)
        depth = np.zeros((4, 4), np.float32).tobytes()
        result, count = native.streaming_depth(triangles.tobytes(), depth, 4, 4, 16)
        assert result == depth
        assert count == 16

    @pytest.mark.parametrize(
        "triangles,depth,width,height,remaining",
        [
            (b"x", bytes(16), 2, 2, 1),
            (b"", b"", 2, 2, 1),
            (b"", b"", 0, 2, 1),
            (b"", bytes(16), 2, 2, 20_000_001),
        ],
        ids=["triangles", "depth", "dimensions", "budget"],
    )
    def test_rejects_invalid_boundary(self, triangles, depth, width, height, remaining):
        with pytest.raises(ValueError, match="invalid"):
            native.streaming_depth(triangles, depth, width, height, remaining)

    def test_rejects_nonfinite_coordinates(self):
        with pytest.raises(ValueError, match="finite"):
            native.streaming_depth(
                np.full(9, np.nan, np.float32).tobytes(), bytes(16), 2, 2, 4
            )
