"""Qualified OBJ arrays preserve analytical geometry before avoiding scene startup.

Unsupported layouts keep the canonical loader, including textures and materials.
A partial parse must never become a successful mesh.
"""

from dataclasses import replace

import numpy as np
import pytest

from app.core.config import _overlay
from app.modules.media import obj_source
from app.modules.media.array_measurements import geometry_from_arrays
from app.modules.media.mesh_loading import load_mesh
from app.modules.media.mesh_measurements import geometry_from_mesh

_TETRA = (
    b"v 0 0 0\nv 10 0 0\nv 1 20 0\nv 2 3 30\n"
    b"vn 0 0 -1\nvn 0 -1 0\nvn -1 0 0\nvn 1 1 1\n"
    b"f 1//1 3//1 2//1\nf 1//2 2//2 4//2\n"
    b"f 1//3 4//3 3//3\nf 2//4 3//4 4//4\n"
)


class TestLoadObjSource:
    @pytest.mark.parametrize(
        "suffix",
        [b"", b"g second\nl 1 2\np 3\n", b"s off\n"],
        ids=["plain", "ignored-polyline-points", "smoothing"],
    )
    def test_preserves_source_corners(self, tmp_path, suffix):
        source = tmp_path / "source.obj"
        source.write_bytes(_TETRA + suffix)
        expected = load_mesh(source)

        actual = obj_source.load_obj_source(source)

        assert actual is not None
        np.testing.assert_array_equal(
            actual.vertices[actual.faces], expected.vertices[expected.faces]
        )

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(_TETRA, id="split-normals-closed"),
            pytest.param(_TETRA.replace(b"f 2//4 3//4 4//4\n", b""), id="open"),
            pytest.param(
                _TETRA.replace(b"f 2//4 3//4 4//4", b"f 4//4 3//4 2//4"),
                id="inconsistent-winding",
            ),
            pytest.param(
                _TETRA.replace(b"//2", b"//1")
                .replace(b"//3", b"//1")
                .replace(b"//4", b"//1"),
                id="shared-normals-closed",
            ),
        ],
    )
    def test_preserves_volume_evidence(self, tmp_path, payload):
        source = tmp_path / "source.obj"
        source.write_bytes(payload)
        expected = geometry_from_mesh(load_mesh(source))
        arrays = obj_source.load_obj_source(source)
        assert arrays is not None

        actual = geometry_from_arrays(arrays)

        assert actual == expected

    def test_keeps_distinct_corner_pairs_at_index_boundaries(self, tmp_path):
        source = tmp_path / "source.obj"
        source.write_bytes(
            b"v 0 0 0\nv 10 0 0\nv 0 20 0\n"
            b"vn 0 0 1\nvn 0 0 1\n"
            b"f 1//2 2//1 3//2\nf 1//2 2//2 3//1\n"
        )

        actual = obj_source.load_obj_source(source)

        assert actual is not None
        np.testing.assert_array_equal(actual.faces, [[0, 1, 4], [0, 2, 3]])
        np.testing.assert_array_equal(
            actual.vertices, [[0, 0, 0], [10, 0, 0], [10, 0, 0], [0, 20, 0], [0, 20, 0]]
        )

    def test_preserves_normal_index_seams(self, tmp_path):
        source = tmp_path / "source.obj"
        source.write_bytes(_TETRA)

        actual = obj_source.load_obj_source(source)

        assert actual is not None
        assert len(actual.vertices) == 12
        assert len(np.unique(actual.faces)) == 12

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(_TETRA + b"mtllib surface.mtl\n", id="materials"),
            pytest.param(_TETRA + b"# mtllib surface.mtl\n", id="embedded-material"),
            pytest.param(_TETRA + b"vt 0 0\n", id="texture"),
            pytest.param(_TETRA + b"o part\n", id="object"),
            pytest.param(
                _TETRA.replace(b"f 1//1 3//1 2//1", b"f 1//1 3//1 2//1 4//1"), id="quad"
            ),
            pytest.param(_TETRA.replace(b"1//1", b"-4//1"), id="relative-index"),
            pytest.param(_TETRA.replace(b"1//1", b"1/1/1"), id="uv-index"),
            pytest.param(_TETRA.replace(b"f 1//1", b" f 1//1"), id="indented-face"),
            pytest.param(_TETRA.replace(b"f 1//1", b"f\t1//1"), id="tab-directive"),
            pytest.param(_TETRA.replace(b"1//1", b"1_0//1"), id="python-only-integer"),
            pytest.param(_TETRA + b"f 1 2 3\n", id="mixed-index-layout"),
            pytest.param(_TETRA + b"\\\n", id="continuation"),
        ],
    )
    def test_delegates_unsupported_layouts(self, tmp_path, payload):
        source = tmp_path / "source.obj"
        source.write_bytes(payload)

        result = obj_source.load_obj_source(source)

        assert result is None

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(b"", id="empty"),
            pytest.param(b"# no faces\n", id="no-vertices"),
            pytest.param(_TETRA.replace(b"10 0 0", b"nan 0 0"), id="nonfinite"),
            pytest.param(_TETRA.replace(b"10 0 0", b"bad 0 0"), id="bad-number"),
            pytest.param(_TETRA.replace(b"10 0 0", b"10 0"), id="missing-coordinate"),
            pytest.param(_TETRA.replace(b"1//1", b"0//1"), id="zero-index"),
            pytest.param(_TETRA.replace(b"1//1", b"5//1"), id="missing-vertex"),
            pytest.param(_TETRA.replace(b"1//1", b"1//5"), id="missing-normal"),
            pytest.param(
                _TETRA.replace(b"1//1", b"999999999999999999999//1"),
                id="index-overflow",
            ),
            pytest.param(_TETRA + b"l 1\n", id="short-line"),
            pytest.param(_TETRA + b"p 0\n", id="zero-point"),
            pytest.param(_TETRA + b"p 5\n", id="missing-point"),
            pytest.param(_TETRA + b"\x00", id="nul"),
        ],
    )
    def test_delegates_malformed_input(self, tmp_path, payload):
        source = tmp_path / "source.obj"
        source.write_bytes(payload)

        result = obj_source.load_obj_source(source)

        assert result is None

    @pytest.mark.parametrize(
        "boundary,value",
        [("_MAX_SOURCE_BYTES", len(_TETRA) - 1), ("_MAX_LINE_BYTES", 8)],
        ids=["source-bytes", "line-bytes"],
    )
    def test_bounds_source_allocations(self, tmp_path, monkeypatch, boundary, value):
        source = tmp_path / "source.obj"
        source.write_bytes(_TETRA)
        monkeypatch.setattr(obj_source, boundary, value)

        result = obj_source.load_obj_source(source)

        assert result is None

    def test_bounds_geometry_allocations(self, tmp_path, monkeypatch):
        source = tmp_path / "source.obj"
        source.write_bytes(_TETRA)
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)

        result = obj_source.load_obj_source(source)

        assert result is None

    def test_delegates_expired_reads(self, tmp_path, monkeypatch):
        source = tmp_path / "source.obj"
        source.write_bytes(_TETRA)
        clock = iter([0.0, 46.0])
        monkeypatch.setattr(obj_source.time, "monotonic", lambda: next(clock))

        result = obj_source.load_obj_source(source)

        assert result is None

    def test_delegates_changed_sources(self, tmp_path, monkeypatch):
        source = tmp_path / "source.obj"
        source.write_bytes(_TETRA)
        snapshot = obj_source.STLSourceSnapshot.from_stat(source.stat())
        values = iter([snapshot, replace(snapshot, mtime_ns=snapshot.mtime_ns + 1)])
        monkeypatch.setattr(
            obj_source.STLSourceSnapshot, "from_stat", lambda _: next(values)
        )

        result = obj_source.load_obj_source(source)

        assert result is None

    def test_delegates_unavailable_sources(self, tmp_path):
        result = obj_source.load_obj_source(tmp_path / "missing.obj")

        assert result is None
