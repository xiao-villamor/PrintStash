"""Binary STL loading preserves every stored triangle without scene copies."""

import struct

import numpy as np
import printstash_mesh_native as native
import pytest
import trimesh


@pytest.fixture
def binary_stl(tmp_path):
    path = tmp_path / "cube.stl"
    path.write_bytes(trimesh.creation.box(extents=[2, 3, 4]).export(file_type="stl"))
    return path


class TestLoadBinaryStl:
    def test_preserves_vertex_order(self, binary_stl):
        expected = trimesh.load_mesh(binary_stl, process=False)

        vertices, _ = native.load_binary_stl(binary_stl)

        np.testing.assert_array_equal(
            np.frombuffer(vertices, dtype=np.float64).reshape(-1, 3), expected.vertices
        )

    def test_preserves_face_order(self, binary_stl):
        expected = trimesh.load_mesh(binary_stl, process=False)

        _, faces = native.load_binary_stl(binary_stl)

        np.testing.assert_array_equal(
            np.frombuffer(faces, dtype=np.int64).reshape(-1, 3), expected.faces
        )

    def test_ignores_stored_normals_for_geometry(self, binary_stl):
        expected = native.load_binary_stl(binary_stl)
        data = bytearray(binary_stl.read_bytes())
        data[84:96] = struct.pack("<fff", 99, 88, 77)
        data[132:134] = b"\xff\xff"
        binary_stl.write_bytes(data)

        assert native.load_binary_stl(binary_stl) == expected

    def test_binary_header_may_start_with_solid(self, binary_stl):
        expected = native.load_binary_stl(binary_stl)
        data = bytearray(binary_stl.read_bytes())
        data[:5] = b"solid"
        binary_stl.write_bytes(data)

        assert native.load_binary_stl(binary_stl) == expected

    def test_defers_ascii_stl(self, tmp_path):
        path = tmp_path / "ascii.stl"
        path.write_text(trimesh.creation.box().export(file_type="stl_ascii"))

        assert native.load_binary_stl(path) is None

    def test_defers_short_input(self, tmp_path):
        path = tmp_path / "short.stl"
        path.write_bytes(b"solid empty\nendsolid empty\n")

        assert native.load_binary_stl(path) is None

    def test_handles_empty_binary_mesh(self, tmp_path):
        path = tmp_path / "empty.stl"
        path.write_bytes(bytes(84))

        assert native.load_binary_stl(path) == (b"", b"")

    def test_bounds_expanded_bytes(self, binary_stl):
        with pytest.raises(ValueError, match="limit"):
            native.load_binary_stl(binary_stl, max_bytes=binary_stl.stat().st_size + 1)

    def test_propagates_missing_file(self, tmp_path):
        with pytest.raises(OSError):
            native.load_binary_stl(tmp_path / "missing.stl")

    def test_bounds_input_bytes(self, tmp_path):
        path = tmp_path / "empty.stl"
        path.write_bytes(bytes(84))

        with pytest.raises(ValueError, match="limit"):
            native.load_binary_stl(path, max_bytes=83)

    def test_defers_mismatched_length(self, binary_stl):
        binary_stl.write_bytes(binary_stl.read_bytes()[:-1])

        assert native.load_binary_stl(binary_stl) is None

    def test_rejects_nonfinite_vertices(self, binary_stl):
        data = bytearray(binary_stl.read_bytes())
        data[96:100] = struct.pack("<f", float("nan"))
        binary_stl.write_bytes(data)

        with pytest.raises(ValueError, match="finite"):
            native.load_binary_stl(binary_stl)
