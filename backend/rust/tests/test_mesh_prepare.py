"""Native normals respect analytic geometry and validate all indices before use."""

import numpy as np
import printstash_mesh_native as native
import pytest


@pytest.fixture
def preparation():
    return dict(
        vertices=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32).tobytes(),
        faces=np.array([[0, 1, 2]], np.int64).tobytes(),
        positions=np.arange(3, dtype=np.int64).tobytes(),
        position_count=3,
        chunk_size=1,
    )


class TestSmoothNormals:
    @pytest.mark.parametrize("limit", ["faces", "vertices"])
    def test_rejects_meshes_beyond_resource_limits(self, preparation, limit):
        if limit == "faces":
            preparation["faces"] = bytes(2_000_001 * 24)
        else:
            preparation["vertices"] = bytes(6_000_001 * 12)
            preparation["positions"] = bytes(6_000_001 * 8)
        with pytest.raises(ValueError, match="buffers"):
            native.smooth_normals(**preparation)

    def test_preserves_a_planar_normal(self, preparation):
        result = native.smooth_normals(**preparation)
        np.testing.assert_array_equal(
            np.frombuffer(result, np.float64).reshape(-1, 3), [[0, 0, 1]] * 3
        )

    def test_keeps_degenerate_normals_zero(self, preparation):
        preparation["vertices"] = bytes(36)
        assert native.smooth_normals(**preparation) == bytes(72)

    def test_rejects_arithmetic_overflow(self, preparation):
        preparation["vertices"] = np.array(
            [[0, 0, 0], [1e30, 0, 0], [0, 1e30, 0]], np.float32
        ).tobytes()
        with pytest.raises(ValueError, match="overflow"):
            native.smooth_normals(**preparation)

    @pytest.mark.parametrize(
        "key,value",
        [
            ("faces", np.array([0, 1, 3], np.int64).tobytes()),
            ("positions", np.array([0, -1, 2], np.int64).tobytes()),
            ("vertices", np.full(9, np.inf, np.float32).tobytes()),
        ],
        ids=["face-index", "position-index", "nonfinite"],
    )
    def test_rejects_invalid_values(self, preparation, key, value):
        preparation[key] = value
        with pytest.raises(ValueError, match="invalid mesh"):
            native.smooth_normals(**preparation)

    @pytest.mark.parametrize(
        "key,value",
        [
            ("chunk_size", 0),
            ("position_count", 4),
            ("faces", b"x"),
            ("vertices", b"x"),
            ("positions", b"x"),
        ],
        ids=["empty-chunk", "position-count", "faces", "vertices", "positions"],
    )
    def test_rejects_invalid_buffers(self, preparation, key, value):
        preparation[key] = value
        with pytest.raises(ValueError, match="buffers"):
            native.smooth_normals(**preparation)
