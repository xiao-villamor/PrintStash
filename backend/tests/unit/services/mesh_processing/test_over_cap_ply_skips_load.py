"""Defends over cap ply skips load at the services mesh processing unit boundary.

A regression could exceed mesh budgets or publish an incomplete render.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from ._mesh_limits_shared import (
    Path,
    _fake_mesh,
    _over_cap_3mf_with_preview,
    _overlay,
    _real_binary_stl_cube,
    _valid_preview_png,
    _write_binary_stl,
    _write_obj,
    mesh_processing,
    np,
    zipfile,
)


class _MemoryHungryProcess:
    pid = 4242

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


def test_over_cap_ply_skips_load(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "dense.ply"
    p.write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement face 999999\nend_header\n"
    )
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("over-cap PLY must not load")),
    )

    geometry = mesh_processing.extract_geometry(p)
    assert geometry["triangle_count"] is None


def test_extract_geometry_respects_cap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "huge.stl"
    _write_binary_stl(p, 50_000)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )
    assert mesh_processing.extract_geometry(p)["triangle_count"] is None


def test_render_thumbnail_respects_cap_and_falls_back_to_embedded(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    png = _valid_preview_png()
    p = tmp_path / "dense.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)  # over cap
        zf.writestr("Metadata/thumbnail.png", png)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    assert mesh_processing.render_thumbnail(p) == png


def test_to_stl_bytes_refuses_over_cap_mesh(tmp_path: Path, monkeypatch) -> None:
    # A download-as-STL click on a monster 3MF/OBJ must not run an unbounded
    # trimesh.load_mesh (which would OOM the process for every user). Refuse cleanly.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "dense.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)  # over cap
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    assert mesh_processing.to_stl_bytes(p) is None


def test_to_stl_bytes_passes_through_raw_stl(tmp_path: Path) -> None:
    # An STL is returned byte-for-byte without any load, so the cap never applies
    # (no conversion, no memory blow-up) even for a large file.
    p = tmp_path / "raw.stl"
    _write_binary_stl(p, 10)
    assert mesh_processing.to_stl_bytes(p) == p.read_bytes()


def test_obj_triangle_count_from_face_directives(tmp_path: Path) -> None:
    p = tmp_path / "mesh.obj"
    _write_obj(p, tri_faces=300)
    # 300 triangular faces -> 300 triangles (exact for tris).
    assert mesh_processing._estimate_triangle_count(p) == 300


def test_obj_ngon_faces_count_conservatively(tmp_path: Path) -> None:
    p = tmp_path / "quads.obj"
    _write_obj(p, tri_faces=10, quads=5)  # 10 + 5*(4-2) = 20 triangles
    assert mesh_processing._estimate_triangle_count(p) == 20


def test_obj_without_faces_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "points.obj"
    p.write_bytes(b"v 0 0 0\nv 1 0 0\nvn 0 0 1\n")
    assert mesh_processing._estimate_triangle_count(p) is None


def test_render_jobs_limit_floors_at_one(monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "max_render_jobs", 0)
    assert mesh_processing._render_jobs_limit() == 1
    monkeypatch.setitem(_overlay, "max_render_jobs", -5)
    assert mesh_processing._render_jobs_limit() == 1


def test_ram_cap_divides_budget_by_max_render_jobs(monkeypatch) -> None:
    # Same RAM, same fraction — doubling the concurrent-job count halves the
    # per-job triangle cap.
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 4 * 1024**3)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

    monkeypatch.setitem(_overlay, "max_render_jobs", 1)
    one = mesh_processing._ram_triangle_cap(".stl")
    monkeypatch.setitem(_overlay, "max_render_jobs", 2)
    two = mesh_processing._ram_triangle_cap(".stl")

    assert one == int(2 * 1024**3 / mesh_processing._DEFAULT_PEAK_BYTES_PER_TRIANGLE)
    assert two == one // 2


def test_render_semaphore_caps_concurrent_renders(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "max_render_jobs", 2)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    # Drop any cached semaphore built at a different limit by an earlier test.
    monkeypatch.setattr(mesh_processing, "_RENDER_SEMAPHORE", None)

    p = tmp_path / "ok.stl"
    _write_binary_stl(p, 500)
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(500))

    state = {"current": 0, "peak": 0}
    entered = threading.Condition()
    release = threading.Event()

    def _slow_render(*_a, **_k):
        with entered:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            entered.notify_all()
        assert release.wait(timeout=5), "render workers were not released"
        with entered:
            state["current"] -= 1
        return b"PNG"

    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", _slow_render
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(mesh_processing.analyze_mesh, p) for _ in range(8)]
        try:
            with entered:
                assert entered.wait_for(lambda: state["current"] == 2, timeout=5), (
                    "two render workers did not acquire the configured slots"
                )
            release.set()
            for future in futures:
                future.result(timeout=5)
        finally:
            release.set()

    assert state["peak"] == 2


def test_large_3mf_uses_embedded_preview_when_flag_on(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)  # 3MF is over cap
    monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", True)
    p, png = _over_cap_3mf_with_preview(tmp_path)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("large 3MF must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] is None  # never loaded
    assert thumb == png  # embedded preview used instead


def test_large_3mf_skips_embedded_preview_when_flag_off(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    monkeypatch.setitem(_overlay, "use_embedded_3mf_preview_for_large_files", False)
    p, _png = _over_cap_3mf_with_preview(tmp_path)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("large 3MF must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] is None
    assert thumb is None  # flag off → no embedded fallback for the over-cap file


def test_over_cap_obj_skips_load(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "dense.obj"
    _write_obj(p, tri_faces=5000)  # well over the cap
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("over-cap OBJ must not load")),
    )

    assert mesh_processing.extract_geometry(p)["triangle_count"] is None
    assert mesh_processing.render_thumbnail(p) is None
    assert mesh_processing.to_stl_bytes(p) is None


def test_estimator_returns_none_for_unrecognised_suffix(tmp_path: Path) -> None:
    p = tmp_path / "part.step"
    p.write_bytes(b"not a real STEP file")
    assert mesh_processing._estimate_triangle_count(p) is None


def test_estimator_returns_none_for_corrupt_3mf(tmp_path: Path) -> None:
    p = tmp_path / "corrupt.3mf"
    p.write_bytes(b"not actually a zip")
    assert mesh_processing._estimate_triangle_count(p) is None


def test_ply_header_without_end_header_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "truncated.ply"
    # File ends mid-header, before an "end_header" line is ever seen.
    p.write_bytes(b"ply\nformat ascii 1.0\nelement vertex 3\n")
    assert mesh_processing._estimate_triangle_count(p) is None


def test_ply_face_count_non_integer_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad-count.ply"
    p.write_bytes(b"ply\nformat ascii 1.0\nelement face notanumber\nend_header\n")
    assert mesh_processing._estimate_triangle_count(p) is None


def test_detect_memory_limit_survives_unreadable_sources(monkeypatch) -> None:
    from pathlib import Path as _Path

    read_text = Mock(side_effect=OSError("no such file"))
    monkeypatch.setattr(_Path, "read_text", read_text)

    assert mesh_processing._detect_memory_limit_bytes() is None
    assert read_text.call_count == 3


def test_detect_memory_limit_reads_cgroup_v2_value(monkeypatch) -> None:
    from pathlib import Path as _Path

    read_text = Mock(
        side_effect=["2147483648\n", OSError("cgroup v1 absent"), OSError("no proc")]
    )
    monkeypatch.setattr(_Path, "read_text", read_text)

    assert mesh_processing._detect_memory_limit_bytes() == 2147483648


def test_detect_memory_limit_reads_cgroup_v1_value(monkeypatch) -> None:
    from pathlib import Path as _Path

    read_text = Mock(
        side_effect=[OSError("cgroup v2 absent"), "1073741824\n", OSError("no proc")]
    )
    monkeypatch.setattr(_Path, "read_text", read_text)

    assert mesh_processing._detect_memory_limit_bytes() == 1073741824


def test_detect_memory_limit_ignores_unlimited_cgroup_v2(monkeypatch) -> None:
    from pathlib import Path as _Path

    read_text = Mock(side_effect=["max\n", OSError("absent"), OSError("no proc")])
    monkeypatch.setattr(_Path, "read_text", read_text)

    assert mesh_processing._detect_memory_limit_bytes() is None


def test_ram_triangle_cap_uses_cached_memory_limit(monkeypatch) -> None:
    # _MEMORY_LIMIT_BYTES already resolved (not None) -> _detect_memory_limit_bytes
    # is never called again.
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 4 * 1024**3)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)

    def _boom():  # pragma: no cover - must never run
        raise AssertionError("must reuse cached limit")

    monkeypatch.setattr(mesh_processing, "_detect_memory_limit_bytes", _boom)
    assert mesh_processing._ram_triangle_cap(".stl") is not None


def test_ram_triangle_cap_none_when_detection_fails(monkeypatch) -> None:
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", None)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
    monkeypatch.setattr(mesh_processing, "_detect_memory_limit_bytes", lambda: None)
    assert mesh_processing._ram_triangle_cap(".stl") is None


def test_render_jobs_limit_falls_back_to_one_on_bad_config(monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "max_render_jobs", "not-a-number")
    assert mesh_processing._render_jobs_limit() == 1


def test_exceeds_cap_survives_stat_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
    p = tmp_path / "ghost.stl"
    _write_binary_stl(p, 10)

    def fake_stat(self):
        raise OSError("gone")

    monkeypatch.setattr(Path, "stat", fake_stat)
    # size_mb falls back to 0.0 on OSError, so the size cap can't trip; the
    # (also-mocked-out) triangle estimate then decides. Real stat is restored
    # by monkeypatch teardown.
    assert mesh_processing._exceeds_cap(p) is False


def test_load_mesh_returns_trimesh_for_real_stl(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    mesh = mesh_processing._load_mesh(p)
    assert mesh is not None
    assert len(mesh.faces) > 0


def test_step_tessellation_is_killed_when_child_exceeds_rss_budget(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "complex.step"
    path.write_text("ISO-10303-21;")

    process = _MemoryHungryProcess()
    monkeypatch.setattr(mesh_processing.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(mesh_processing, "_step_memory_budget_bytes", lambda: 1024)
    monkeypatch.setattr(mesh_processing, "_process_rss_bytes", lambda _pid: 2048)

    assert mesh_processing._load_step_mesh_isolated(path) is None
    assert process.killed is True


def test_load_mesh_returns_none_for_unrecognised_extension(tmp_path: Path) -> None:
    # trimesh can't even pick a loader for an unknown extension, so this raises
    # inside trimesh.load_mesh — exercising _load_mesh's broad except-and-log path.
    p = tmp_path / "garbage.foobar"
    p.write_bytes(b"this is not a mesh at all \x00\x01\x02")
    assert mesh_processing._load_mesh(p) is None


def test_load_mesh_flattens_scene_with_multiple_geometries(
    tmp_path: Path, monkeypatch
) -> None:
    # trimesh.load_mesh(...) already concatenates a multi-geometry
    # Scene into one Trimesh internally, so _load_mesh's own Scene-flattening
    # branch is normally unreachable through that call. Stub trimesh.load_mesh to
    # return a real Scene so this (still-real) fallback path is exercised —
    # it's a legitimate defensive path for a future/edge-case trimesh return.
    import trimesh

    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(extents=[5, 5, 5]), node_name="a")
    scene.add_geometry(
        trimesh.creation.box(extents=[3, 3, 3]).apply_translation([10, 0, 0]),
        node_name="b",
    )
    p = tmp_path / "scene.3mf"
    scene.export(p, file_type="3mf")

    monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: scene)
    mesh = mesh_processing._load_mesh(p)
    assert mesh is not None
    # Concatenated geometry from both boxes.
    assert len(mesh.faces) == 24  # 12 triangles per box * 2


def test_load_mesh_scene_with_no_trimesh_geometry_returns_none(
    tmp_path: Path, monkeypatch
) -> None:
    import trimesh

    empty_scene = trimesh.Scene()  # no geometry at all
    p = tmp_path / "empty.3mf"
    p.write_bytes(b"placeholder")
    monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: empty_scene)
    assert mesh_processing._load_mesh(p) is None


def test_load_mesh_scene_with_single_geometry_returns_it_directly(
    tmp_path: Path, monkeypatch
) -> None:
    import trimesh

    scene = trimesh.Scene()
    box = trimesh.creation.box(extents=[5, 5, 5])
    scene.add_geometry(box, node_name="a")
    p = tmp_path / "single.3mf"
    p.write_bytes(b"placeholder")
    monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: scene)
    mesh = mesh_processing._load_mesh(p)
    assert mesh is not None
    assert len(mesh.faces) == 12


def test_load_mesh_returns_none_for_unsupported_loaded_type(
    tmp_path: Path, monkeypatch
) -> None:
    import trimesh

    p = tmp_path / "cloud.stl"
    p.write_bytes(b"placeholder")
    # A defensive loader may return a PointCloud (or other non-mesh geometry) for
    # some inputs; _load_mesh must decline rather than mishandle it.
    monkeypatch.setattr(
        trimesh,
        "load_mesh",
        lambda *a, **k: trimesh.points.PointCloud([[0, 0, 0]]),
    )
    assert mesh_processing._load_mesh(p) is None


def test_load_mesh_uses_typed_loader_without_processing(
    tmp_path: Path, monkeypatch
) -> None:
    import trimesh

    expected = trimesh.creation.box(extents=[1, 1, 1])
    calls: list[tuple[tuple, dict]] = []

    def typed_loader(*args, **kwargs):
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(trimesh, "load_scene", typed_loader)
    path = tmp_path / "typed.stl"
    path.write_bytes(b"placeholder")

    assert mesh_processing._load_mesh(path) is expected
    assert calls == [((str(path),), {"process": False})]


def test_geometry_from_mesh_handles_non_watertight_volume_error(monkeypatch) -> None:
    class _BrokenVolume:
        vertices = np.zeros((3, 3), dtype=np.float64)
        bounds = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
        faces = np.zeros((1, 3), dtype=np.int64)

        @property
        def volume(self):
            raise ValueError("non-watertight")

    geometry = mesh_processing._geometry_from_mesh(_BrokenVolume())
    assert geometry["volume_mm3"] is None
    assert geometry["bbox_x_mm"] == 1.0


def test_extract_embedded_3mf_thumbnail_no_candidates_returns_none(
    tmp_path: Path,
) -> None:
    p = tmp_path / "no-preview.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<mesh/>")
    assert mesh_processing.extract_embedded_3mf_thumbnail(p) is None


def test_extract_embedded_3mf_thumbnail_survives_corrupt_archive(
    tmp_path: Path,
) -> None:
    p = tmp_path / "corrupt.3mf"
    p.write_bytes(b"not a zip archive")
    assert mesh_processing.extract_embedded_3mf_thumbnail(p) is None


def test_analyze_mesh_reports_progress_labels(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    labels: list[str] = []
    geometry, thumb = mesh_processing.analyze_mesh(p, report=labels.append)
    assert labels == ["loading_mesh", "extracting_geometry", "rendering_thumbnail"]
    assert geometry["triangle_count"] is not None
    assert thumb is not None
