"""STL export applies loading caps before materialization. Instanced 3MF transforms survive conversion, while invalid sources and export failures return no bytes."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.similarity import GeometryError

from app.core.config import _overlay
from app.modules.media import (
    mesh_loading,
    mesh_policy,
)
from tests.fixtures.three_mf_projects import (
    build_3d_builder_component_project,
    build_instanced_project,
)

from .._meshes import (
    _real_binary_stl_cube,
    _write_binary_stl,
    _write_obj,
)


def _forbid_trimesh_scene_load(monkeypatch) -> list[str]:
    """Record every `trimesh.load_scene` call instead of raising from it.

    `_load_mesh` swallows exceptions and answers `None`, so a stub that raises
    makes "the guard refused" and "the unbounded loader ran and failed" look the
    same. A recorded call cannot be mistaken for a refusal.
    """
    calls: list[str] = []

    def record(*args, **kwargs):
        calls.append(str(args[0]) if args else "")
        raise RuntimeError("unbounded trimesh scene load")

    monkeypatch.setattr(trimesh, "load_scene", record)
    return calls


class TestToStlBytes:
    def test_to_stl_bytes_refuses_over_cap_mesh(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A download-as-STL click on a monster 3MF/OBJ must not run an unbounded
        # trimesh.load_mesh (which would OOM the process for every user). Refuse cleanly.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        p = tmp_path / "dense.obj"
        _write_obj(p, 5000)
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )

        assert mesh_loading.to_stl_bytes(p) is None

    def test_to_stl_bytes_passes_through_raw_stl(self, tmp_path: Path) -> None:
        # An STL is returned byte-for-byte without any load, so the cap never applies
        # (no conversion, no memory blow-up) even for a large file.
        p = tmp_path / "raw.stl"
        _write_binary_stl(p, 10)
        assert mesh_loading.to_stl_bytes(p) == p.read_bytes()

    def test_to_stl_bytes_bakes_the_scene_transforms_into_the_geometry(
        self, tmp_path: Path
    ) -> None:
        """A 3MF places its parts with transforms; STL has nowhere to put them.

        3D Builder and most CAD exporters write one mesh and position it through
        a nested build/component graph, so the vertices in the file are at the
        origin and the placement lives in the matrices above them. Converting the
        mesh without flattening that graph produces an STL of a part sitting at
        0,0,0 — the preview and the download both show something that is not what
        the user modelled, with no error to explain it.
        """
        path = tmp_path / "3d-builder-component.3mf"
        path.write_bytes(build_3d_builder_component_project())

        converted = mesh_loading.to_stl_bytes(path)

        assert converted is not None and len(converted) > 84
        mesh = trimesh.load_mesh(io.BytesIO(converted), file_type="stl", process=False)
        np.testing.assert_allclose(
            mesh.bounds,
            np.asarray([[110.0, 220.0, 330.0], [112.0, 223.0, 334.0]]),
            atol=1e-5,
        )

    def test_to_stl_bytes_refuses_a_3mf_whose_placements_exceed_the_budget(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """#259: the byte-size estimate cannot see repeated component placements.

        300 placements of a 12-triangle part is ~20 KiB of XML, which the size
        estimate prices at ~300 triangles — far under the cap — while the
        expanded scene is 3,600. trimesh expands placements while it loads, so
        the guard has to be the resource loader, which counts the expanded faces
        before it composes anything. The viewer route reaches this function.
        """
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        path = tmp_path / "instanced.3mf"
        path.write_bytes(build_instanced_project(300))
        assert not mesh_policy.exceeds_cap(
            path
        )  # the bounded reader owns placement cost
        scene_loads = _forbid_trimesh_scene_load(monkeypatch)

        with pytest.raises(GeometryError, match="scene_resource_limit"):
            mesh_loading.to_stl_bytes(path)
        assert scene_loads == []

    def test_to_stl_bytes_converts_an_instanced_3mf_inside_the_budget(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        path = tmp_path / "few.3mf"
        path.write_bytes(build_instanced_project(10))
        scene_loads = _forbid_trimesh_scene_load(monkeypatch)

        converted = mesh_loading.to_stl_bytes(path)

        assert converted is not None
        mesh = trimesh.load_mesh(io.BytesIO(converted), file_type="stl", process=False)
        assert len(mesh.faces) == 120
        assert scene_loads == []

    def test_to_stl_bytes_fails_closed_on_a_3mf_it_cannot_open(
        self, tmp_path: Path
    ) -> None:
        """Malformed 3MF produces a typed refusal for the caller to project."""
        path = tmp_path / "malformed.3mf"
        path.write_bytes(b"not a zip archive")

        with pytest.raises(GeometryError, match="invalid_3mf"):
            mesh_loading.to_stl_bytes(path)

    def test_to_stl_bytes_read_failure_returns_none(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        p = tmp_path / "cube.stl"
        _write_binary_stl(p, 10)

        def fake_read_bytes(self):
            raise OSError("disk gone")

        monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
        assert mesh_loading.to_stl_bytes(p) is None

    def test_to_stl_bytes_converts_non_stl_mesh(self, tmp_path: Path) -> None:
        import trimesh

        p = tmp_path / "cube.obj"
        trimesh.creation.box(extents=[4, 4, 4]).export(p, file_type="obj")
        out = mesh_loading.to_stl_bytes(p)
        assert out is not None
        assert out[80:84] != b""

    def test_to_stl_bytes_returns_none_when_mesh_fails_to_load(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "garbage.foobar"
        p.write_bytes(b"not a mesh file \x00\x01")
        assert mesh_loading.to_stl_bytes(p) is None

    def test_to_stl_bytes_returns_none_on_export_failure(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        # Force the "already an STL" fast-path to miss by faking a different suffix
        # so we exercise the load+export branch, then make export blow up.
        obj_path = tmp_path / "cube.obj"
        import trimesh

        trimesh.creation.box(extents=[4, 4, 4]).export(obj_path, file_type="obj")

        class _Boom:
            faces = np.zeros((1, 3))

            def export(self, *_a, **_k):
                raise RuntimeError("export boom")

        monkeypatch.setattr(mesh_loading, "load_mesh", lambda _p: _Boom())
        assert mesh_loading.to_stl_bytes(obj_path) is None
