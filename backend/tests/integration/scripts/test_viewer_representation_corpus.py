"""Qualification controls retain independent geometry and stable source encodings."""

import io

import numpy as np
import pytest
import trimesh

from scripts.viewer_representation_corpus import source_builders, write_sources


class TestSources:
    @pytest.mark.parametrize("name", ["ascii-cube", "binary-cube"])
    def test_preserves_geometry_across_stl_encodings(self, tmp_path, name):
        path = write_sources(tmp_path, (name,))[name]
        assert path.suffix == ".stl"
        mesh = trimesh.load(path, process=False)
        assert len(mesh.faces) == 12
        np.testing.assert_array_equal(mesh.bounds, [[0, 0, 0], [20, 20, 20]])
        assert path.read_bytes().startswith(b"solid") == (name == "ascii-cube")

    def test_preserves_overlapping_faces(self):
        mesh = trimesh.load(
            io.BytesIO(source_builders()["overlapping-faces"]()),
            file_type="3mf",
            process=False,
        ).to_mesh()
        assert len(mesh.faces) == 24
        np.testing.assert_array_equal(mesh.bounds, [[0, 0, 0], [20, 20, 20]])
        np.testing.assert_array_equal(mesh.triangles[:12], mesh.triangles[12:])

    def test_preserves_intersecting_placements(self):
        mesh = trimesh.load(
            io.BytesIO(source_builders()["intersecting-solids"]()),
            file_type="3mf",
            process=False,
        ).to_mesh()
        assert len(mesh.faces) == 24
        np.testing.assert_array_equal(mesh.bounds, [[0, 0, 0], [30, 30, 30]])

    @pytest.mark.parametrize(
        "name",
        ["ascii-cube", "binary-cube", "overlapping-faces", "intersecting-solids"],
    )
    def test_produces_deterministic_control_bytes(self, name):
        build = source_builders()[name]
        assert build() == build()
