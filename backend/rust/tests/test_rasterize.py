"""Native geometry must preserve the Python rasterizer's visible fragments."""

import sys

import numpy as np
import printstash_mesh_native as native
import pytest


def fragments(tri, width=16, height=16, depth=None):
    if depth is None:
        depth = np.full((height, width), np.inf, dtype=np.float64)
    payload, count = native.rasterize(
        tri.tobytes(), tri.dtype.itemsize, depth.tobytes(), width, height
    )
    records = np.frombuffer(payload, dtype=np.uint64).reshape(-1, 3)
    return records[:, 0], records[:, 1], records[:, 2].view(np.float64), count


class TestRasterize:
    @pytest.mark.parametrize("dtype", [np.float32, np.float64])
    def test_paints_a_triangle(self, dtype):
        tri = np.array([[[0, 0, 3], [4, 0, 3], [0, 4, 3]]], dtype=dtype)
        pixels, faces, depth, _ = fragments(tri)
        assert pixels.tolist() == [0, 1, 2, 3, 16, 17, 18, 32, 33, 48]
        np.testing.assert_array_equal(faces, 0)
        np.testing.assert_array_equal(depth, 3)

    def test_nearest_face_wins(self):
        tri = np.array([[[0, 0, 3], [4, 0, 3], [0, 4, 3]]], dtype=np.float32)
        nearer = tri.copy()
        nearer[:, :, 2] = 1
        _, faces, depth, _ = fragments(np.concatenate([tri, nearer]))
        np.testing.assert_array_equal(faces, 1)
        np.testing.assert_array_equal(depth, 1)

    def test_first_face_wins_equal_depth(self):
        tri = np.array([[[0, 0, 3], [4, 0, 3], [0, 4, 3]]], dtype=np.float32)
        _, faces, _, _ = fragments(np.repeat(tri, 2, axis=0))
        np.testing.assert_array_equal(faces, 0)

    def test_existing_depth_occludes_new_faces(self):
        tri = np.array([[[0, 0, 3], [4, 0, 3], [0, 4, 3]]], dtype=np.float32)
        pixels, _, _, _ = fragments(tri, depth=np.zeros((16, 16)))
        assert len(pixels) == 0

    @pytest.mark.parametrize("tri", [np.empty((0, 3, 3)), np.zeros((1, 3, 3))])
    def test_empty_or_degenerate_faces_paint_nothing(self, tri):
        pixels, _, _, count = fragments(tri)
        assert len(pixels) == count == 0

    def test_clips_to_the_image(self):
        tri = np.array(
            [[[-100, -100, 0], [100, -100, 0], [0, 100, 0]]], dtype=np.float64
        )
        pixels, _, _, _ = fragments(tri)
        assert pixels.tolist() == list(range(256))

    @pytest.mark.parametrize(
        "data,itemsize,depth,width,height,match",
        [
            (b"", 2, b"", 1, 1, "float width"),
            (b"x", 4, b"", 1, 1, "triangle buffer"),
            (b"", 4, b"", 1, 1, "depth buffer"),
            (b"", 4, b"", 0, 1, "dimensions"),
            (b"", 4, b"", 65536, 65536, "dimensions"),
            (b"", 4, b"", sys.maxsize, 3, "dimensions"),
            (
                np.full((1, 3, 3), np.nan).tobytes(),
                8,
                np.zeros(1).tobytes(),
                1,
                1,
                "finite",
            ),
        ],
    )
    def test_rejects_invalid_buffers(self, data, itemsize, depth, width, height, match):
        with pytest.raises(ValueError, match=match):
            native.rasterize(data, itemsize, depth, width, height)
