"""Point preprocessing keeps the source checkpoint's grouping contract."""

import numpy as np
import pytest

from printstash_core.inference import EmbeddingError, EmbeddingInput
from printstash_core.inference.points import canary_input, grouped_points, point_input


class TestPointInput:
    def test_preserves_the_bounded_tensor(self):
        item = canary_input()
        assert item.modality == "point_cloud"
        assert len(item.points) == 240000
        assert EmbeddingInput("point_cloud", points=item.points) == item

    @pytest.mark.parametrize(
        "values",
        [
            {"points": b""},
            {"points": b"x" * 240001},
            {"points": b"x" * 240000, "text": "mixed"},
            {"points": b"x" * 240000, "rgb": b"abc"},
            {"points": b"x" * 240000, "width": 1},
        ],
    )
    def test_rejects_wrong_tensor_contract(self, values):
        with pytest.raises(EmbeddingError, match="embedding_input_invalid"):
            EmbeddingInput("point_cloud", **values)

    @pytest.mark.parametrize(
        "channel,value", [(0, np.nan), (0, np.inf), (0, 2), (3, -0.1), (3, 1.1)]
    )
    def test_rejects_unbounded_tensor_values(self, channel, value):
        data = (
            np.frombuffer(canary_input().points, dtype="<f4")
            .copy()
            .reshape(1, 6, 10000)
        )
        data[0, channel, 0] = value
        with pytest.raises(EmbeddingError, match="embedding_point_input_invalid"):
            grouped_points(EmbeddingInput("point_cloud", points=data.tobytes()))

    def test_groups_by_source_order_with_radius_padding(self):
        # First center has only two neighbours. The second FPS center is the
        # remote point; all ties retain source order, not distance order.
        values = np.full((1, 6, 10000), 0.4, dtype="<f4")
        values[0, :3] = 0.9
        values[0, :3, 0] = 0
        values[0, :3, 1] = 0.1
        centers, grouped = grouped_points(
            EmbeddingInput("point_cloud", points=values.tobytes())
        )
        np.testing.assert_array_equal(centers[0, :, 0], [0, 0, 0])
        np.testing.assert_allclose(centers[0, :, 1], [0.9, 0.9, 0.9])
        np.testing.assert_allclose(grouped[0, :3, 1, 0], [0.1, 0.1, 0.1])
        np.testing.assert_array_equal(grouped[0, :3, 2:, 0], np.zeros((3, 254)))
        assert grouped.shape == (1, 9, 256, 64)
        np.testing.assert_allclose(grouped[0, 6:, :, 0], 0.4)

    def test_normalizes_shared_surface_samples(self):
        vertices = np.asarray(
            [[0, 0, 0], [2, 0, 0], [0, 3, 0], [0, 0, 4]], dtype=np.float64
        )
        faces = np.asarray([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]], dtype=np.int64)
        first = point_input(vertices, faces)
        translated = point_input(vertices + 10, faces)
        points = np.frombuffer(first.points, dtype="<f4").reshape(1, 6, 10000)
        assert first == translated
        assert np.linalg.norm(points[0, :3], axis=0).max() == pytest.approx(1)
        np.testing.assert_allclose(points[0, :3].mean(axis=1), 0, atol=1e-7)
        np.testing.assert_allclose(points[0, 3:], 0.4)

    def test_rejects_near_zero_geometry_radius(self):
        vertices = np.asarray(
            [[0, 0, 0], [1e-13, 0, 0], [0, 1e-13, 0], [0, 0, 1e-13]], dtype=np.float64
        )
        faces = np.asarray([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]], dtype=np.int64)
        with pytest.raises(EmbeddingError, match="embedding_point_geometry_invalid"):
            point_input(vertices, faces)

    def test_rejects_nonpoint_grouping_inputs(self):
        with pytest.raises(EmbeddingError, match="embedding_point_input_invalid"):
            grouped_points(EmbeddingInput("text", text="a bracket"))
