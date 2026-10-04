"""The temporary scalar geometry facade delegates to the bounded owners. Legacy callers receive dimensions only when source loading is admitted, and loaded meshes are released."""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh
from printstash_core.mesh.similarity import GeometryError

from app.core.config import _overlay
from app.modules.media import (
    mesh_loading,
    mesh_policy,
    mesh_processing,
    native_process,
)
from tests.fixtures.three_mf_projects import (
    build_instanced_project,
)

from .._meshes import (
    _real_binary_stl_cube,
    _write_binary_stl,
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


class TestExtractGeometry:
    def test_extract_geometry_loads_the_mesh_exactly_once(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        calls = {"n": 0}
        monkeypatch.setattr(
            mesh_policy,
            "reclaim_memory",
            lambda: calls.__setitem__("n", calls["n"] + 1),
        )
        geometry = mesh_processing.extract_geometry(p)
        assert geometry["triangle_count"] is not None
        assert calls["n"] == 1

    def test_extract_geometry_respects_cap(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        p = tmp_path / "huge.stl"
        _write_binary_stl(p, 50_000)
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
        )
        assert mesh_processing.extract_geometry(p)["triangle_count"] is None

    def test_over_cap_ply_skips_load(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        p = tmp_path / "dense.ply"
        p.write_bytes(
            b"ply\nformat binary_little_endian 1.0\nelement face 999999\nend_header\n"
        )
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(
                AssertionError("over-cap PLY must not load")
            ),
        )

        geometry = mesh_processing.extract_geometry(p)
        assert geometry["triangle_count"] is None

    def test_ram_cap_skips_mesh_a_big_host_would_render(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Static ceiling is generous (5M), but a 2 GB host can't afford this mesh.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 5_000_000)
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: 2 * 1024**3)
        p = tmp_path / "mid.stl"
        # ~700k triangles: under the 5M static cap, but over the ~480k RAM cap @ 2 GB.
        _write_binary_stl(p, 700_000)
        assert mesh_policy.ram_triangle_cap(".stl") < 700_000

        def _boom(_path):  # pragma: no cover
            raise AssertionError("RAM-capped mesh must not load")

        monkeypatch.setattr(mesh_loading, "load_mesh", _boom)
        assert mesh_processing.extract_geometry(p)["triangle_count"] is None

    def test_static_cap_still_applies_on_a_huge_ram_host(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A 256 GB host: the RAM cap is enormous, so the static ceiling is what binds.
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: 256 * 1024**3)
        p = tmp_path / "huge.stl"
        _write_binary_stl(p, 50_000)
        monkeypatch.setattr(
            mesh_loading,
            "load_mesh",
            lambda _p: (_ for _ in ()).throw(
                AssertionError("over static cap must not load")
            ),
        )
        assert mesh_processing.extract_geometry(p)["triangle_count"] is None

    def test_extract_geometry_refuses_a_3mf_whose_placements_exceed_the_budget(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        path = tmp_path / "instanced.3mf"
        path.write_bytes(build_instanced_project(300))
        scene_loads = _forbid_trimesh_scene_load(monkeypatch)

        with pytest.raises(GeometryError, match="scene_resource_limit"):
            mesh_processing.extract_geometry(path)
        assert scene_loads == []
