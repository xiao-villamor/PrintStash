"""Visual loading keeps geometry bounded without requiring topology objects."""

from __future__ import annotations

import numpy as np
import pytest
from printstash_core.mesh.similarity import GeometryError

from app.core.config import _overlay
from app.modules.media.visual_loading import load_visual_mesh
from tests.factories import content


class TestLoadVisualMesh:
    @pytest.mark.parametrize("suffix", [".stl", ".obj"], ids=["stl", "obj"])
    def test_retains_source_coordinates(self, tmp_path, suffix):
        payloads = {
            ".stl": content.binary_stl_facets(
                [((100, 0, 0), (110, 0, 0), (100, 10, 0))]
            ),
            ".obj": b"v 100 0 0\nv 110 0 0\nv 100 10 0\nf -3/-1 -2/-1 -1/-1\n",
        }
        source = tmp_path / ("source" + suffix)
        source.write_bytes(payloads[suffix])

        mesh = load_visual_mesh(source, suffix)

        np.testing.assert_array_equal(
            mesh.vertices, [[100, 0, 0], [110, 0, 0], [100, 10, 0]]
        )
        np.testing.assert_array_equal(mesh.faces, [[0, 1, 2]])

    @pytest.mark.parametrize(
        ("face", "expected"),
        [
            pytest.param(b"f 1 2 3 4", [[0, 1, 2], [2, 3, 0]], id="quad"),
            pytest.param(
                b"f 1 2 3 4 5", [[0, 1, 2], [0, 2, 3], [0, 3, 4]], id="polygon"
            ),
        ],
    )
    def test_triangulates_obj_polygons(self, tmp_path, face, expected):
        source = tmp_path / "source.obj"
        source.write_bytes(
            b"v 0 0 0\nv 10 0 0\nv 10 10 0\nv 0 10 0\nv -10 0 0\n" + face
        )

        mesh = load_visual_mesh(source, ".obj")

        np.testing.assert_array_equal(mesh.faces, expected)

    @pytest.mark.parametrize(
        ("suffix", "payload"),
        [
            pytest.param(".stl", content.binary_stl()[:-1], id="truncated-stl"),
            pytest.param(
                ".stl",
                content.binary_stl_facets(
                    [((float("nan"), 0, 0), (1, 0, 0), (0, 1, 0))]
                ),
                id="nonfinite-stl",
            ),
            pytest.param(".obj", b"v nan 0 0\nf 1 1 1", id="nonfinite-obj"),
            pytest.param(".obj", b"v 0 0 0\nf 0 1 1", id="zero-index"),
            pytest.param(".obj", b"v 0 0 0\nf 2 1 1", id="missing-index"),
            pytest.param(".obj", b"v 0 0 0\nf -2 1 1", id="negative-index"),
            pytest.param(".obj", b"v 0 0\nf 1 1 1", id="incomplete-vertex"),
            pytest.param(".obj", b"v 0 0 0\nf 1 1", id="incomplete-face"),
            pytest.param(".obj", b"# empty", id="empty"),
        ],
    )
    def test_refuses_invalid_visual_source(self, tmp_path, suffix, payload):
        source = tmp_path / ("source" + suffix)
        source.write_bytes(payload)

        with pytest.raises(GeometryError):
            load_visual_mesh(source, suffix)

    @pytest.mark.parametrize("suffix", [".stl", ".obj"], ids=["stl", "obj"])
    def test_bounds_visual_allocation(self, tmp_path, monkeypatch, suffix):
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
        source = tmp_path / ("source" + suffix)
        source.write_bytes(
            {
                ".stl": content.binary_stl(triangles=2),
                ".obj": b"v 0 0 0\nf 1 1 1\nf 1 1 1",
            }[suffix]
        )

        with pytest.raises(GeometryError, match="resource_limit"):
            load_visual_mesh(source, suffix)

    def test_bounds_obj_lines(self, tmp_path):
        source = tmp_path / "source.obj"
        source.write_bytes(b"#" * (64 * 1024 + 1))

        with pytest.raises(GeometryError, match="resource_limit"):
            load_visual_mesh(source, ".obj")

    def test_reports_unavailable_source(self, tmp_path):
        with pytest.raises(GeometryError, match="source_unavailable"):
            load_visual_mesh(tmp_path / "missing.obj", ".obj")

    def test_reads_continued_obj_faces(self, tmp_path):
        source = tmp_path / "source.obj"
        source.write_bytes(
            b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 " + bytes([92, 10]) + b"2 3\n"
        )

        mesh = load_visual_mesh(source, ".obj")

        np.testing.assert_array_equal(mesh.faces, [[0, 1, 2]])

    def test_bounds_source_bytes(self, tmp_path, monkeypatch):
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1 / 1024**2)
        source = tmp_path / "source.obj"
        source.write_bytes(b"v 0 0 0\nf 1 1 1")

        with pytest.raises(GeometryError, match="resource_limit"):
            load_visual_mesh(source, ".obj")
