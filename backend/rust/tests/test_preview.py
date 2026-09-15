"""Owned native frames preserve visibility across batches and reject failed output."""

import numpy as np
import printstash_mesh_native as native
import pytest


@pytest.fixture
def triangle():
    return np.array([[[0, 0, 1], [4, 0, 1], [0, 4, 1]]], dtype=np.float64)


class TestNativeFrame:
    def test_retains_nearest_color_across_batches(self, triangle):
        frame = native.NativeFrame(4, 4)
        frame.draw_flat(triangle.tobytes(), 8, (10, 20, 30))
        frame.draw_flat((triangle + [0, 0, 1]).tobytes(), 8, (40, 50, 60))

        pixels = np.frombuffer(frame.rgba(), np.uint8).reshape(4, 4, 4)
        np.testing.assert_array_equal(pixels[0, 0], [10, 20, 30, 255])
        np.testing.assert_array_equal(pixels[3, 3], [0, 0, 0, 0])

    def test_keeps_first_face_at_equal_depth(self, triangle):
        frame = native.NativeFrame(4, 4)
        frame.draw_flat(triangle.tobytes(), 8, (10, 20, 30))
        expected = frame.rgba()
        frame.draw_flat(triangle.tobytes(), 8, (40, 50, 60))

        assert frame.rgba() == expected

    def test_discards_a_failed_frame(self, triangle):
        frame = native.NativeFrame(4, 4)
        invalid = np.concatenate([triangle, np.full_like(triangle, np.nan)])
        with pytest.raises(ValueError, match="finite"):
            frame.draw_flat(invalid.tobytes(), 8, (10, 20, 30))

        with pytest.raises(RuntimeError, match="cannot be published"):
            frame.rgba()

    def test_failed_frame_refuses_further_draws(self, triangle):
        frame = native.NativeFrame(4, 4)
        with pytest.raises(ValueError, match="finite"):
            frame.draw_flat(np.full_like(triangle, np.nan).tobytes(), 8, (1, 2, 3))

        with pytest.raises(RuntimeError, match="cannot be published"):
            frame.draw_flat(triangle.tobytes(), 8, (1, 2, 3))

    def test_invalid_shading_input_preserves_the_previous_frame(self, triangle):
        frame = native.NativeFrame(4, 4)
        frame.draw_flat(triangle.tobytes(), 8, (10, 20, 30))
        expected = frame.rgba()

        with pytest.raises(ValueError, match="normal"):
            frame.draw_phong(triangle.tobytes(), 8, b"bad", 8, [0.0] * 31)

        assert frame.rgba() == expected

    @pytest.mark.parametrize(
        "dimensions",
        [(0, 1), (1, 0), (2**32, 2**32), (16_777_217, 1)],
        ids=["width", "height", "overflow", "pixel-limit"],
    )
    def test_rejects_invalid_dimensions(self, dimensions):
        with pytest.raises(ValueError, match="dimensions"):
            native.NativeFrame(*dimensions)

    def test_rejects_mutable_triangle_storage(self, triangle):
        frame = native.NativeFrame(4, 4)
        with pytest.raises(TypeError, match="bytes"):
            frame.draw_flat(bytearray(triangle.tobytes()), 8, (1, 2, 3))

    def test_empty_frame_is_transparent(self):
        assert native.NativeFrame(4, 4).rgba() == bytes(64)
