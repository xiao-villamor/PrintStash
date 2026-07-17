"""Mesh-density cap that stops one dense lattice/gyroid file from OOM-killing the
process during a library scan (issue #24).

Loading + rasterising a mesh costs ~700 MB of peak RSS per million triangles,
and the cost is paid inside ``trimesh.load`` — so the only safe defence is to
estimate the triangle count *before* loading and skip the monster. The file is
still indexed; a 3MF still yields its embedded slicer preview.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import _overlay
from app.services import mesh_processing


@pytest.fixture(autouse=True)
def _static_cap_only():
    """Most tests here exercise the *static* triangle/byte caps, whose outcome
    must not depend on the CI host's RAM. Disable the RAM-aware cap by default;
    the dedicated RAM-cap tests re-enable it explicitly."""
    prev = _overlay.get("mesh_memory_budget_fraction", "__unset__")
    _overlay["mesh_memory_budget_fraction"] = 0
    yield
    if prev == "__unset__":
        _overlay.pop("mesh_memory_budget_fraction", None)
    else:
        _overlay["mesh_memory_budget_fraction"] = prev


def _write_binary_stl(path: Path, n_triangles: int) -> None:
    """A minimal but structurally valid binary STL with *n_triangles* facets."""
    with path.open("wb") as fh:
        fh.write(b"\x00" * 80)  # header
        fh.write(struct.pack("<I", n_triangles))
        fh.write(b"\x00" * (50 * n_triangles))  # 50 bytes per facet


def _fake_mesh(num_faces: int):
    return SimpleNamespace(
        vertices=np.zeros((3, 3), dtype=np.float64),
        bounds=np.array([[0.0, 0.0, 0.0], [10.0, 20.0, 30.0]]),
        faces=np.zeros((num_faces, 3), dtype=np.int64),
        volume=42.0,
    )


def test_binary_stl_triangle_count_is_exact(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _write_binary_stl(p, 1234)
    assert mesh_processing._estimate_triangle_count(p) == 1234


def test_3mf_triangle_count_from_uncompressed_xml(tmp_path: Path) -> None:
    p = tmp_path / "dense.3mf"
    model_xml = b"<triangle/>" * 10_000  # 110_000 bytes of "mesh"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", model_xml)
    # ~70 bytes per triangle proxy.
    assert mesh_processing._estimate_triangle_count(p) == len(model_xml) // 70


def test_over_cap_mesh_is_never_loaded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    p = tmp_path / "huge.stl"
    _write_binary_stl(p, 50_000)  # well over the cap

    def _boom(_path):  # pragma: no cover - must never run
        raise AssertionError("over-cap mesh must not be loaded into trimesh")

    monkeypatch.setattr(mesh_processing, "_load_mesh", _boom)

    geometry, thumb = mesh_processing.analyze_mesh(p)

    # Indexed, but with no geometry/thumbnail — and crucially, no load attempt.
    assert geometry["triangle_count"] is None
    assert thumb is None


def test_over_cap_3mf_still_gets_embedded_preview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    png = mesh_processing._PNG_MAGIC + b"preview-bytes"
    p = tmp_path / "dense.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(
            "3D/3dmodel.model", b"<triangle/>" * 100_000
        )  # ~157k tris, over cap
        zf.writestr("Metadata/thumbnail.png", png)

    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)

    assert geometry["triangle_count"] is None  # mesh skipped
    assert thumb == png  # but the cheap embedded preview is still used


def test_post_load_backstop_skips_render_when_estimate_missed(
    tmp_path: Path, monkeypatch
) -> None:
    # A format the estimator can't size up (returns None) but whose loaded mesh
    # is over budget: keep the cheap geometry, skip the expensive render.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 10)
    p = tmp_path / "model.obj"
    p.write_text("# obj")

    monkeypatch.setattr(mesh_processing, "_estimate_triangle_count", lambda _p: None)
    monkeypatch.setattr(
        mesh_processing, "_load_mesh", lambda _p: _fake_mesh(num_faces=99)
    )
    monkeypatch.setattr(
        mesh_processing.mesh_render,
        "render_mesh_thumbnail",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not render")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)

    assert geometry["triangle_count"] == 99  # cheap geometry kept
    assert thumb is None  # render skipped


def test_under_cap_mesh_renders_normally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
    p = tmp_path / "ok.stl"
    _write_binary_stl(p, 500)

    monkeypatch.setattr(
        mesh_processing, "_load_mesh", lambda _p: _fake_mesh(num_faces=500)
    )
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNGDATA"
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)

    assert geometry["triangle_count"] == 500
    assert thumb == b"PNGDATA"


# ---------------------------------------------------------------------------
# RAM-aware cap: the effective triangle ceiling scales down with available
# memory so a small host skips meshes a large host renders (issue #29).
# ---------------------------------------------------------------------------


def test_detect_memory_limit_is_positive_on_linux() -> None:
    limit = mesh_processing._detect_memory_limit_bytes()
    # On Linux CI this reads /proc/meminfo or a cgroup; elsewhere it may be None.
    assert limit is None or limit > 0


def test_ram_cap_disabled_when_fraction_zero(monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
    assert mesh_processing._ram_triangle_cap(".stl") is None


def test_ram_cap_scales_with_memory_and_format(monkeypatch) -> None:
    # Pin a 4 GB ceiling so the result is host-independent.
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 4 * 1024**3)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
    stl_cap = mesh_processing._ram_triangle_cap(".stl")
    mf_cap = mesh_processing._ram_triangle_cap(".3mf")
    # 2 GB budget / per-triangle cost.
    assert stl_cap == int(
        2 * 1024**3 / mesh_processing._DEFAULT_PEAK_BYTES_PER_TRIANGLE
    )
    assert mf_cap == int(2 * 1024**3 / mesh_processing._PEAK_BYTES_PER_TRIANGLE[".3mf"])
    # 3MF is the heavier format, so its cap is the lower of the two.
    assert mf_cap < stl_cap


def test_ram_cap_skips_mesh_a_big_host_would_render(
    tmp_path: Path, monkeypatch
) -> None:
    # Static ceiling is generous (5M), but a 2 GB host can't afford this mesh.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 5_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 2 * 1024**3)
    p = tmp_path / "mid.stl"
    # ~700k triangles: under the 5M static cap, but over the ~480k RAM cap @ 2 GB.
    _write_binary_stl(p, 700_000)
    assert mesh_processing._ram_triangle_cap(".stl") < 700_000

    def _boom(_path):  # pragma: no cover
        raise AssertionError("RAM-capped mesh must not load")

    monkeypatch.setattr(mesh_processing, "_load_mesh", _boom)
    assert mesh_processing.extract_geometry(p)["triangle_count"] is None


def test_static_cap_still_applies_on_a_huge_ram_host(
    tmp_path: Path, monkeypatch
) -> None:
    # A 256 GB host: the RAM cap is enormous, so the static ceiling is what binds.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
    monkeypatch.setattr(mesh_processing, "_MEMORY_LIMIT_BYTES", 256 * 1024**3)
    p = tmp_path / "huge.stl"
    _write_binary_stl(p, 50_000)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(
            AssertionError("over static cap must not load")
        ),
    )
    assert mesh_processing.extract_geometry(p)["triangle_count"] is None


# ---------------------------------------------------------------------------
# Per-file memory reclamation: a loaded mesh's arrays are freed and returned to
# the OS between files so a long scan's RSS doesn't only ever climb (issue #29).
# ---------------------------------------------------------------------------


def test_loaded_mesh_triggers_memory_reclaim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    p = tmp_path / "ok.stl"
    _write_binary_stl(p, 500)

    calls = {"n": 0}
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(500))
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNG"
    )
    monkeypatch.setattr(
        mesh_processing,
        "_reclaim_memory",
        lambda: calls.__setitem__("n", calls["n"] + 1),
    )

    mesh_processing.analyze_mesh(p)
    assert calls["n"] == 1  # freed exactly once, after the mesh was used


def test_skipped_mesh_does_not_reclaim(tmp_path: Path, monkeypatch) -> None:
    # No mesh was loaded (over cap), so there's nothing to free — and we don't pay
    # gc.collect()/malloc_trim for a file we never touched.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)
    p = tmp_path / "huge.stl"
    _write_binary_stl(p, 50_000)

    calls = {"n": 0}
    monkeypatch.setattr(
        mesh_processing,
        "_reclaim_memory",
        lambda: calls.__setitem__("n", calls["n"] + 1),
    )
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    mesh_processing.analyze_mesh(p)
    assert calls["n"] == 0


def test_reclaim_memory_is_safe_to_call() -> None:
    # Must never raise, regardless of libc/platform — it's best-effort cleanup.
    mesh_processing._reclaim_memory()


# ---------------------------------------------------------------------------
# Raw byte-size guard: the format-blind backstop for files the triangle
# estimate can't size up (issue #29 — a ~900 MB 3MF that OOM-killed the scan).
# ---------------------------------------------------------------------------


def test_oversize_file_is_never_loaded(tmp_path: Path, monkeypatch) -> None:
    # Triangle cap is generous so it can't be what trips the guard; the file is
    # only ~2 MB of facets (well under it). The 1 MB *size* cap must still skip
    # the load — this is the path that protects against an estimator that comes
    # up empty on a huge file.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
    p = tmp_path / "big.stl"
    _write_binary_stl(p, 42_000)  # ~2 MB on disk
    assert p.stat().st_size > 1024 * 1024

    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("oversize file must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] is None
    assert thumb is None


def test_oversize_3mf_still_gets_embedded_preview(tmp_path: Path, monkeypatch) -> None:
    # A 3MF over the byte cap is never decompressed into trimesh, but the cheap
    # embedded slicer preview (read straight from the zip) still stands in.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
    png = mesh_processing._PNG_MAGIC + b"preview"
    p = tmp_path / "big.3mf"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 200_000)  # ~2 MB stored
        zf.writestr("Metadata/thumbnail.png", png)
    assert p.stat().st_size > 1024 * 1024

    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _p: (_ for _ in ()).throw(AssertionError("oversize 3MF must not load")),
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] is None
    assert thumb == png


def test_size_guard_disabled_when_zero(tmp_path: Path, monkeypatch) -> None:
    # mesh_max_load_mb = 0 turns the byte cap off; a big-but-sparse-triangle file
    # then loads normally (only the triangle cap still applies).
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    p = tmp_path / "big.stl"
    _write_binary_stl(p, 42_000)

    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(42_000))
    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", lambda *a, **k: b"PNG"
    )

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] == 42_000
    assert thumb == b"PNG"


def test_3mf_without_model_part_falls_back_to_total_uncompressed_size(
    tmp_path: Path,
) -> None:
    # No ".model" entry: the estimator must not return None (which would let the
    # archive load blind). It falls back to the total uncompressed payload as a
    # conservative upper bound (issue #29).
    p = tmp_path / "weird.3mf"
    payload = b"x" * 700_000
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("3D/mesh.bin", payload)
    est = mesh_processing._estimate_triangle_count(p)
    assert est == len(payload) // 70


# ---------------------------------------------------------------------------
# Estimator: binary-vs-ASCII STL disambiguation (the dangerous direction).
# ---------------------------------------------------------------------------


def test_binary_stl_with_trailing_bytes_is_not_underestimated(tmp_path: Path) -> None:
    # Some exporters append metadata after the facet block, so the exact
    # 84 + 50*N size check fails. The old code fell back to the ASCII estimate
    # (size // 250), underestimating a binary file ~5x and letting an over-cap
    # mesh slip through to an OOM load. The body-size estimate must stay a safe
    # upper bound on the real triangle count.
    p = tmp_path / "trailing.stl"
    n = 100_000
    _write_binary_stl(p, n)
    with p.open("ab") as fh:
        fh.write(b"exported by SomeSlicer\x00\x01\x02" * 50)  # trailing junk

    est = mesh_processing._estimate_triangle_count(p)
    assert est is not None
    assert est >= n  # never below the true count (the OOM-unsafe direction)
    # And nowhere near the 5x-low ASCII misread.
    assert est < n * 2


def test_ascii_stl_is_detected_and_estimated_by_text_density(tmp_path: Path) -> None:
    facet = (
        b"  facet normal 0 0 1\n"
        b"    outer loop\n"
        b"      vertex 0 0 0\n"
        b"      vertex 1 0 0\n"
        b"      vertex 0 1 0\n"
        b"    endloop\n"
        b"  endfacet\n"
    )
    p = tmp_path / "ascii.stl"
    p.write_bytes(b"solid mymesh\n" + facet * 300 + b"endsolid mymesh\n")

    est = mesh_processing._estimate_triangle_count(p)
    # ASCII estimate is size // 250; the file holds 300 real facets, and the
    # estimate should land in the same order of magnitude (not the 5x-too-low
    # binary misread of size // 50-equivalents).
    assert est == p.stat().st_size // 250
    assert est > 0


def test_binary_stl_header_starting_with_solid_is_not_misread_as_ascii(
    tmp_path: Path,
) -> None:
    # The classic STL trap: a binary STL whose 80-byte header text starts with
    # "solid". The NUL bytes in the binary body must keep it on the binary path.
    p = tmp_path / "trap.stl"
    n = 60_000
    with p.open("wb") as fh:
        fh.write(b"solid exported-by-tool".ljust(80, b"\x00"))
        fh.write(struct.pack("<I", n))
        fh.write(b"\x00" * (50 * n))
    with p.open("ab") as fh:
        fh.write(b"trailer")  # break the exact size match

    est = mesh_processing._estimate_triangle_count(p)
    assert est is not None
    assert est >= n  # treated as binary, not the 5x-low ASCII estimate


# ---------------------------------------------------------------------------
# Estimator: PLY face count from the header (no body parse).
# ---------------------------------------------------------------------------


def test_ply_face_count_from_header(tmp_path: Path) -> None:
    p = tmp_path / "scan.ply"
    header = (
        b"ply\n"
        b"format binary_little_endian 1.0\n"
        b"element vertex 8\n"
        b"property float x\n"
        b"property float y\n"
        b"property float z\n"
        b"element face 1234567\n"
        b"property list uchar int vertex_indices\n"
        b"end_header\n"
    )
    # Body is intentionally tiny/garbage — the estimate must come from the header
    # alone, never from loading the (declared-huge) body.
    p.write_bytes(header + b"\x00" * 32)

    assert mesh_processing._estimate_triangle_count(p) == 1234567


def test_ply_without_face_element_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "points.ply"
    p.write_bytes(
        b"ply\nformat ascii 1.0\nelement vertex 3\n"
        b"property float x\nend_header\n0 0 0\n"
    )
    assert mesh_processing._estimate_triangle_count(p) is None


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


# ---------------------------------------------------------------------------
# The cap is enforced on every entry point, not just analyze_mesh.
# ---------------------------------------------------------------------------


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
    png = mesh_processing._PNG_MAGIC + b"plate"
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
    # trimesh.load (which would OOM the process for every user). Refuse cleanly.
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


# ---------------------------------------------------------------------------
# Estimator: OBJ face-directive count (a first-class type that was unguarded).
# ---------------------------------------------------------------------------


def _write_obj(path: Path, tri_faces: int, *, quads: int = 0) -> None:
    lines = [b"# comment\n", b"o mesh\n", b"v 0 0 0\n", b"vn 0 0 1\n"]
    lines += [b"f 1//1 2//1 3//1\n"] * tri_faces
    lines += [b"f 1 2 3 4\n"] * quads  # quad = 2 triangles after fan
    path.write_bytes(b"".join(lines))


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


# ---------------------------------------------------------------------------
# Concurrency-aware RAM budget: the per-job triangle cap divides by
# VAULT_MAX_RENDER_JOBS, and a semaphore caps how many renders run at once so a
# bulk upload's background tasks can't collectively OOM the box (issue #29).
# ---------------------------------------------------------------------------


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
    import threading
    import time

    monkeypatch.setitem(_overlay, "max_render_jobs", 2)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    # Drop any cached semaphore built at a different limit by an earlier test.
    monkeypatch.setattr(mesh_processing, "_RENDER_SEMAPHORE", None)

    p = tmp_path / "ok.stl"
    _write_binary_stl(p, 500)
    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(500))

    state = {"current": 0, "peak": 0}
    lock = threading.Lock()

    def _slow_render(*_a, **_k):
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        time.sleep(0.05)  # hold the slot so overlap is observable
        with lock:
            state["current"] -= 1
        return b"PNG"

    monkeypatch.setattr(
        mesh_processing.mesh_render, "render_mesh_thumbnail", _slow_render
    )

    threads = [
        threading.Thread(target=lambda: mesh_processing.analyze_mesh(p))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["peak"] >= 1  # work really ran
    assert state["peak"] <= 2  # never more than VAULT_MAX_RENDER_JOBS at once


# ---------------------------------------------------------------------------
# Large-3MF embedded-preview preference: a 3MF over the adaptive cap uses its
# embedded slicer preview without ever decompressing/parsing the mesh — gated by
# VAULT_USE_EMBEDDED_3MF_PREVIEW_FOR_LARGE_FILES (issue #29).
# ---------------------------------------------------------------------------


def _over_cap_3mf_with_preview(tmp_path: Path) -> tuple[Path, bytes]:
    png = mesh_processing._PNG_MAGIC + b"slicer-plate"
    p = tmp_path / "big.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 100_000)  # ~157k tris
        zf.writestr("Metadata/thumbnail.png", png)
    return p, png


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


# ---------------------------------------------------------------------------
# Estimator: unsupported / corrupt formats.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Memory-limit detection: cgroup / meminfo read failures each degrade
# gracefully rather than raising.
# ---------------------------------------------------------------------------


def test_detect_memory_limit_survives_unreadable_sources(monkeypatch) -> None:
    from pathlib import Path as _Path

    real_read_text = _Path.read_text
    unreadable = {
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
        "/proc/meminfo",
    }

    def fake_read_text(self, *a, **k):
        if str(self) in unreadable:
            raise OSError("no such file")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(_Path, "read_text", fake_read_text)
    assert mesh_processing._detect_memory_limit_bytes() is None


def test_detect_memory_limit_reads_cgroup_v2_value(monkeypatch) -> None:
    from pathlib import Path as _Path

    real_read_text = _Path.read_text

    def fake_read_text(self, *a, **k):
        if str(self) == "/sys/fs/cgroup/memory.max":
            return "2147483648\n"  # 2 GB
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(_Path, "read_text", fake_read_text)
    limit = mesh_processing._detect_memory_limit_bytes()
    assert limit is not None
    assert limit <= 2147483648  # smallest of cgroup v2 and any other source


def test_detect_memory_limit_reads_cgroup_v1_value(monkeypatch) -> None:
    from pathlib import Path as _Path

    real_read_text = _Path.read_text

    def fake_read_text(self, *a, **k):
        if str(self) == "/sys/fs/cgroup/memory.max":
            raise OSError("cgroup v2 absent")
        if str(self) == "/sys/fs/cgroup/memory/memory.limit_in_bytes":
            return "1073741824\n"  # 1 GB
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(_Path, "read_text", fake_read_text)
    limit = mesh_processing._detect_memory_limit_bytes()
    assert limit is not None
    assert limit <= 1073741824


def test_detect_memory_limit_ignores_unlimited_cgroup_v2(monkeypatch) -> None:
    from pathlib import Path as _Path

    real_read_text = _Path.read_text

    def fake_read_text(self, *a, **k):
        if str(self) == "/sys/fs/cgroup/memory.max":
            return "max\n"
        if str(self) == "/sys/fs/cgroup/memory/memory.limit_in_bytes":
            raise OSError("absent")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(_Path, "read_text", fake_read_text)
    # Falls through to /proc/meminfo (real, host-dependent) or None.
    limit = mesh_processing._detect_memory_limit_bytes()
    assert limit is None or limit > 0


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


# ---------------------------------------------------------------------------
# Real mesh loading + geometry/thumbnail entry points (no mocked _load_mesh).
# ---------------------------------------------------------------------------


def _real_binary_stl_cube(path: Path) -> None:
    import trimesh

    trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(path, file_type="stl")


def test_load_mesh_returns_trimesh_for_real_stl(tmp_path: Path) -> None:
    p = tmp_path / "cube.stl"
    _real_binary_stl_cube(p)
    mesh = mesh_processing._load_mesh(p)
    assert mesh is not None
    assert len(mesh.faces) > 0


def test_load_mesh_returns_none_for_unrecognised_extension(tmp_path: Path) -> None:
    # trimesh can't even pick a loader for an unknown extension, so this raises
    # inside trimesh.load — exercising _load_mesh's broad except-and-log path.
    p = tmp_path / "garbage.foobar"
    p.write_bytes(b"this is not a mesh at all \x00\x01\x02")
    assert mesh_processing._load_mesh(p) is None


def test_load_mesh_flattens_scene_with_multiple_geometries(
    tmp_path: Path, monkeypatch
) -> None:
    # trimesh.load(..., force="mesh") already concatenates a multi-geometry
    # Scene into one Trimesh internally, so _load_mesh's own Scene-flattening
    # branch is normally unreachable through that call. Stub trimesh.load to
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

    monkeypatch.setattr(trimesh, "load", lambda *a, **k: scene)
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
    monkeypatch.setattr(trimesh, "load", lambda *a, **k: empty_scene)
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
    monkeypatch.setattr(trimesh, "load", lambda *a, **k: scene)
    mesh = mesh_processing._load_mesh(p)
    assert mesh is not None
    assert len(mesh.faces) == 12


def test_load_mesh_returns_none_for_unsupported_loaded_type(
    tmp_path: Path, monkeypatch
) -> None:
    import trimesh

    p = tmp_path / "cloud.stl"
    p.write_bytes(b"placeholder")
    # trimesh.load can return a PointCloud (or other non-mesh geometry) for
    # some inputs; _load_mesh must decline rather than mishandle it.
    monkeypatch.setattr(
        trimesh, "load", lambda *a, **k: trimesh.points.PointCloud([[0, 0, 0]])
    )
    assert mesh_processing._load_mesh(p) is None


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
    png = mesh_processing._PNG_MAGIC + b"embedded"
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
