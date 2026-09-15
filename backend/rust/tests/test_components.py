"""Edge connectivity retains separate point contacts and canonical face labels."""

import numpy as np
import printstash_mesh_native as native
import pytest


class TestComponentLabels:
    @pytest.mark.parametrize("limit", ["faces", "vertices"])
    def test_rejects_meshes_beyond_resource_limits(self, limit):
        faces = bytes(2_000_001 * 24) if limit == "faces" else b""
        vertex_count = 6_000_001 if limit == "vertices" else 3
        with pytest.raises(ValueError, match="buffers"):
            native.component_labels(faces, vertex_count)

    def test_preserves_point_contacts_as_separate_components(self):
        faces = np.array([[0, 1, 2], [1, 2, 3], [0, 4, 5]], np.int64)
        actual = native.component_labels(faces.tobytes(), 6)
        np.testing.assert_array_equal(np.frombuffer(actual, np.int64), [0, 0, 2])

    def test_connects_all_faces_of_a_nonmanifold_edge(self):
        faces = np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4]], np.int64)
        actual = native.component_labels(faces.tobytes(), 5)
        np.testing.assert_array_equal(np.frombuffer(actual, np.int64), [0, 0, 0])

    def test_accepts_an_empty_mesh(self):
        assert native.component_labels(b"", 0) == b""

    def test_rejects_a_negative_index(self):
        with pytest.raises(ValueError, match="vertex index"):
            native.component_labels(np.array([-1, 0, 1], np.int64).tobytes(), 2)

    def test_rejects_a_truncated_face(self):
        with pytest.raises(ValueError, match="buffers"):
            native.component_labels(b"x", 2)
