"""Defends rendering at the services mesh processing unit boundary.

A regression could exceed mesh budgets or publish an incomplete render.
"""

from __future__ import annotations

from ._mesh_limits_shared import (
    Path,
    _fake_mesh,
    _overlay,
    _real_binary_stl_cube,
    _valid_preview_png,
    _write_binary_stl,
    _write_obj,
    mesh_processing,
    np,
    zipfile,
)


def test_extract_geometry_real_load_and_reclaim(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    calls = {"n": 0}
    monkeypatch.setattr(
        mesh_processing,
        "_reclaim_memory",
        lambda: calls.__setitem__("n", calls["n"] + 1),
    )
    geometry = mesh_processing.extract_geometry(p)
    assert geometry["triangle_count"] is not None
    assert calls["n"] == 1


def test_render_thumbnail_real_mesh_renders_png(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    thumb = mesh_processing.render_thumbnail(p)
    assert thumb is not None
    assert thumb.startswith(mesh_processing._PNG_MAGIC)


def test_render_thumbnail_falls_back_to_embedded_when_render_fails(
    tmp_path: Path, monkeypatch
) -> None:
    png = _valid_preview_png((32, 160, 240))
    p = tmp_path / "model.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<mesh/>")
        zf.writestr("Metadata/thumbnail.png", png)

    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(10))
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: None
    )
    assert mesh_processing.render_thumbnail(p) == png


def test_render_thumbnail_returns_none_when_render_fails_and_no_embedded(
    tmp_path: Path, monkeypatch
) -> None:
    p = tmp_path / "cube.stl"
    _write_binary_stl(p, 10)
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(10))
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: None
    )
    assert mesh_processing.render_thumbnail(p) is None


def test_render_thumbnail_over_cap_with_embedded_fallback_disabled_returns_none(
    tmp_path: Path, monkeypatch
) -> None:
    # Over cap, and the large-file embedded-preview fallback explicitly off:
    # nothing to fall back to, so the function must return None outright.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", False)
    p = tmp_path / "dense.obj"
    _write_obj(p, tri_faces=5000)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("over-cap must not load")),
    )
    assert mesh_processing.render_thumbnail(p) is None


def test_to_stl_bytes_read_failure_returns_none(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "cube.stl"
    _write_binary_stl(p, 10)

    def fake_read_bytes(self):
        raise OSError("disk gone")

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    assert mesh_processing.to_stl_bytes(p) is None


def test_to_stl_bytes_converts_non_stl_mesh(tmp_path: Path) -> None:
    import trimesh

    p = tmp_path / "cube.obj"
    trimesh.creation.box(extents=[4, 4, 4]).export(p, file_type="obj")
    out = mesh_processing.to_stl_bytes(p)
    assert out is not None
    assert out[80:84] != b""  # binary STL triangle-count header present


def test_to_stl_bytes_returns_none_when_mesh_fails_to_load(tmp_path: Path) -> None:
    p = tmp_path / "garbage.foobar"
    p.write_bytes(b"not a mesh file \x00\x01")
    assert mesh_processing.to_stl_bytes(p) is None


def test_to_stl_bytes_returns_none_on_export_failure(
    tmp_path: Path, monkeypatch
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

    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _Boom())
    assert mesh_processing.to_stl_bytes(obj_path) is None
