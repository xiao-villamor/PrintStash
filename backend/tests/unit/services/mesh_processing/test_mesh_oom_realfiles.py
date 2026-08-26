"""Real-file OOM / memory coverage for mesh processing (issue #29).

The cap logic in ``test_mesh_limits.py`` is fast but synthetic — it monkeypatches
``_load_mesh`` so the real trimesh load/render never runs. These tests close that
gap: they build *real* meshes with trimesh and drive the genuine
``analyze_mesh`` path (real load, real rasteriser, real ``_reclaim_memory``), so
a regression in the actual loader/renderer/guard is caught, not just the routing.

Three layers:

* **Real guards** — a real over-triangle mesh and a real over-size file are
  kept away from trimesh but still receive bounded streaming STL geometry and
  thumbnails; a real compression-bomb 3MF is caught without being decompressed.
* **Real happy path** — a real dense mesh still produces geometry + a PNG.
* **Leak detector** — processing the same real mesh many times must not grow
  resident memory, proving ``_reclaim_memory`` actually hands freed buffers back
  to the OS instead of letting a long scan ratchet RSS upward.

A real-world corpus (the user's own NAS files — a ~900 MB 3MF, high-poly scans,
slicer output) can be pointed at via ``PRINTSTASH_MESH_CORPUS``; that test asserts
every file processes within a peak-RSS budget instead of OOM-killing the scan.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import _overlay
from app.services import mesh_processing

# mesh_processing lazy-imports trimesh, so importing it above is safe without it;
# skip the whole module when trimesh itself is unavailable (these build real meshes).
trimesh = pytest.importorskip("trimesh")


@pytest.fixture(autouse=True)
def _static_cap_only():
    """Pin behaviour to the static caps so a CI host's RAM doesn't change which
    real files load (the RAM-aware cap has its own tests in test_mesh_limits)."""
    prev = _overlay.get("mesh_memory_budget_fraction", "__unset__")
    _overlay["mesh_memory_budget_fraction"] = 0
    yield
    if prev == "__unset__":
        _overlay.pop("mesh_memory_budget_fraction", None)
    else:
        _overlay["mesh_memory_budget_fraction"] = prev


# --------------------------------------------------------------------------- #
# Resident-memory helpers (Linux /proc; skip the memory assertions elsewhere).
# --------------------------------------------------------------------------- #
def _proc_kb(field: str) -> int | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(field):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def _rss_kb() -> int | None:
    """Current resident set size in KB (recedes when memory is returned)."""
    return _proc_kb("VmRSS:")


def _peak_rss_kb() -> int | None:
    """High-water resident set size in KB (monotonic)."""
    return _proc_kb("VmHWM:")


_HAVE_PROC_RSS = _rss_kb() is not None


# --------------------------------------------------------------------------- #
# Real mesh builders.
# --------------------------------------------------------------------------- #
def _write_real_stl(path: Path, *, subdivisions: int) -> int:
    """Write a real binary STL sphere and return its triangle count."""
    mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=10.0)
    path.write_bytes(mesh.export(file_type="stl"))
    return len(mesh.faces)


# --------------------------------------------------------------------------- #
# Real guards (genuine trimesh path, no monkeypatching of the loader).
# --------------------------------------------------------------------------- #
def test_real_over_triangle_mesh_uses_streaming_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)  # isolate the triangle cap
    p = tmp_path / "dense.stl"
    tri = _write_real_stl(p, subdivisions=4)  # 5120 real triangles
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", tri // 2)

    # The estimator reads the real binary-STL header, so trimesh is skipped.
    # The bounded STL path still provides useful geometry and a thumbnail.
    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] == tri
    assert geometry["bbox_x_mm"] and geometry["bbox_x_mm"] > 0
    assert isinstance(thumb, mesh_processing.FallbackThumbnail)
    assert thumb.startswith(mesh_processing._PNG_MAGIC)


def test_real_oversize_file_uses_streaming_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    # Triangle cap generous; only the byte cap can trip. A real, perfectly
    # loadable sphere is still skipped purely because the file is over the MB cap.
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100_000_000)
    p = tmp_path / "big.stl"
    tri = _write_real_stl(p, subdivisions=6)  # ~4 MB on disk
    size_mb = p.stat().st_size / (1024 * 1024)
    assert size_mb > 1.0
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] == tri
    assert geometry["bbox_x_mm"] and geometry["bbox_x_mm"] > 0
    assert isinstance(thumb, mesh_processing.FallbackThumbnail)
    assert thumb.startswith(mesh_processing._PNG_MAGIC)


def test_real_compression_bomb_3mf_is_not_decompressed(
    tmp_path: Path, monkeypatch
) -> None:
    # A real ZIP whose .model deflates from a few KB on disk to a huge mesh. The
    # estimate reads the *uncompressed* size from the zip directory and skips it,
    # so trimesh never decompresses the bomb. The embedded preview still stands in.
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    preview = io.BytesIO()
    Image.new("RGB", (8, 8), "navy").save(preview, format="PNG")
    png = preview.getvalue()
    p = tmp_path / "bomb.3mf"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", b"<triangle/>" * 2_000_000)  # tiny on disk
        zf.writestr("Metadata/thumbnail.png", png)
    assert p.stat().st_size < 200_000  # compressed small...
    # ...but the uncompressed estimate is huge, so it's skipped.
    assert mesh_processing._estimate_triangle_count(p) > 1000

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] is None
    assert thumb == png


# --------------------------------------------------------------------------- #
# Real happy path.
# --------------------------------------------------------------------------- #
def test_real_dense_mesh_renders(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000_000)
    p = tmp_path / "sphere.stl"
    tri = _write_real_stl(p, subdivisions=4)

    geometry, thumb = mesh_processing.analyze_mesh(p)
    assert geometry["triangle_count"] == tri
    assert geometry["bbox_x_mm"] and geometry["bbox_x_mm"] > 0
    assert thumb is not None and thumb.startswith(mesh_processing._PNG_MAGIC)


# --------------------------------------------------------------------------- #
# Leak detector: a long scan must not ratchet RSS upward.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _HAVE_PROC_RSS, reason="needs Linux /proc for RSS")
def test_repeated_real_loads_do_not_grow_rss(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_load_mb", 0)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 5_000_000)
    p = tmp_path / "sphere.stl"
    _write_real_stl(p, subdivisions=6)  # ~80k triangles — a real, non-trivial load

    # Warm up: first loads pull in trimesh/numpy machinery and grow the heap once.
    for _ in range(3):
        mesh_processing.analyze_mesh(p)

    baseline = _rss_kb()
    for _ in range(20):
        geometry, thumb = mesh_processing.analyze_mesh(p)
        assert thumb is not None  # real work happened every iteration
    growth_mb = (_rss_kb() - baseline) / 1024

    # With per-file reclamation, steady-state growth is ~0. A real leak (e.g. the
    # mesh retained each iteration) would add tens of MB per loop → hundreds over
    # 20. The 120 MB bound is comfortably between the two.
    assert growth_mb < 120, f"RSS grew {growth_mb:.0f} MB over 20 loads — possible leak"


# --------------------------------------------------------------------------- #
# Opt-in: validate against a real-world corpus of the user's own files.
#   PRINTSTASH_MESH_CORPUS=/path/to/nas/sample  pytest -k corpus -s
#   PRINTSTASH_MESH_RSS_BUDGET_MB=2048           # optional peak-RSS budget
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not os.environ.get("PRINTSTASH_MESH_CORPUS"),
    reason="set PRINTSTASH_MESH_CORPUS to a folder of real mesh files",
)
def test_real_corpus_processes_within_memory_budget() -> None:
    from app.db.models import SUFFIX_TO_FILE_TYPE

    corpus = Path(os.environ["PRINTSTASH_MESH_CORPUS"])
    budget_mb = int(os.environ.get("PRINTSTASH_MESH_RSS_BUDGET_MB", "2048"))
    files = [
        f
        for f in sorted(corpus.rglob("*"))
        if f.is_file() and f.suffix.lower() in SUFFIX_TO_FILE_TYPE
    ]
    assert files, f"no supported mesh/gcode files under {corpus}"

    start_peak = _peak_rss_kb()
    for f in files:
        # Must not raise and must not blow the budget — the whole point of #29.
        mesh_processing.analyze_mesh(f)
        peak = _peak_rss_kb()
        if peak is not None and start_peak is not None:
            peak_mb = (peak - start_peak) / 1024
            assert peak_mb < budget_mb, (
                f"{f.name}: peak RSS climbed {peak_mb:.0f} MB (> {budget_mb} MB budget)"
            )
