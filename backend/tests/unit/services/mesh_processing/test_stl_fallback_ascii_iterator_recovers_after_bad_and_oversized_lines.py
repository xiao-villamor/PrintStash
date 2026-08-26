"""Defends stl fallback ascii iterator recovers after bad and oversized lines at the services mesh processing unit boundary.

A regression could exceed mesh budgets or publish an incomplete render.
"""

from __future__ import annotations

from ._mesh_limits_shared import (
    Path,
    _fake_mesh,
    _overlay,
    _valid_preview_png,
    _write_binary_stl,
    _write_renderable_binary_stl,
    mesh_processing,
    np,
    struct,
    zipfile,
)


def test_stl_fallback_ascii_iterator_recovers_after_bad_and_oversized_lines(
    tmp_path: Path,
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "ascii-iterator.stl"
    valid = b"vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
    path.write_bytes(
        b"solid iterator\n"
        + b"vertex not-a-number 0 0\n"
        + b"vertex nan 0 0\n"
        + b"comment "
        + b"x" * (stl_fallback._MAX_ASCII_LINE_BYTES + 10)
        + b"\n"
        + valid
        + b"endsolid iterator\n"
    )

    records = list(stl_fallback._iter_ascii_triangles(path))
    assert records == [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)]
    assert list(stl_fallback._iter_ascii_triangles(path, max_triangles=0)) == []
    assert list(stl_fallback._iter_ascii_triangles(path, max_lines=0)) == []


def test_stl_fallback_ascii_helpers_fail_closed_on_io_errors(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "ascii-io-error.stl"
    path.write_text("solid empty\n", encoding="ascii")

    def fail_open(_path: Path, *args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "open", fail_open)
    assert list(stl_fallback._iter_ascii_triangles(path)) == []
    assert stl_fallback._read_ascii_samples(path, 1) is None


def test_stl_fallback_ascii_samples_mark_invalid_source_and_truncation(
    tmp_path: Path,
) -> None:
    from app.services import stl_fallback

    path = tmp_path / "ascii-invalid-source.stl"
    path.write_text(
        "solid invalid\nvertex broken 0 0\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n",
        encoding="ascii",
    )
    sampled = stl_fallback._read_ascii_samples(path, 4)
    assert sampled is not None
    assert sampled.parsed_triangles == 1
    assert sampled.complete is False

    empty = tmp_path / "ascii-empty.stl"
    empty.write_text("solid empty\nvertex nan 0 0\n", encoding="ascii")
    assert stl_fallback._read_ascii_samples(empty, 1) is None


def test_stl_fallback_dispatches_binary_and_ascii_iterators(tmp_path: Path) -> None:
    from app.services import stl_fallback

    binary = tmp_path / "dispatch.stl"
    _write_renderable_binary_stl(binary, 1)
    ascii_path = tmp_path / "dispatch-ascii.stl"
    ascii_path.write_text(
        "solid dispatch\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendsolid dispatch\n",
        encoding="ascii",
    )
    assert len(list(stl_fallback._iter_stl_triangles(binary))) == 1
    assert len(list(stl_fallback._iter_stl_triangles(ascii_path))) == 1
    assert stl_fallback._read_binary_samples(binary, 0) is None


def test_stl_fallback_render_rejects_bad_dimensions_and_numeric_states(
    tmp_path: Path, monkeypatch
) -> None:
    from array import array

    from app.services import mesh_render, stl_fallback

    path = tmp_path / "render-defensive.stl"
    _write_renderable_binary_stl(path, 1)
    assert stl_fallback.render_stl_thumbnail(path, width=0) is None
    assert (
        stl_fallback.render_stl_thumbnail(
            path, height=stl_fallback._MAX_RENDER_DIMENSION + 1
        )
        is None
    )

    sampled = stl_fallback._SampledSTL(
        coordinates=array("f", [float("nan")] * 9),
        triangle_count=1,
        sampled_triangles=1,
        bounds_min=(0.0, 0.0, 0.0),
        bounds_max=(1.0, 1.0, 1.0),
        scanned_bytes=1,
        parsed_triangles=1,
        complete=True,
    )
    monkeypatch.setattr(stl_fallback, "_read_samples", lambda *_args: sampled)
    assert stl_fallback.render_stl_thumbnail(path) is None

    sampled.coordinates = array("f", [0.0] * 9)
    sampled.bounds_min = (1e308, 1e308, 1e308)
    sampled.bounds_max = (1e308, 1e308, 1e308)
    assert stl_fallback.render_stl_thumbnail(path) is None

    sampled.bounds_min = (0.0, 0.0, 0.0)
    sampled.bounds_max = (1.0, 1.0, 1.0)
    monkeypatch.setattr(
        mesh_render,
        "_select_view_rotation",
        lambda *_args: (_ for _ in ()).throw(ValueError("rotation")),
    )
    assert stl_fallback.render_stl_thumbnail(path) is None

    sampled.bounds_min = (-1e308, -1e308, -1e308)
    sampled.bounds_max = (1e308, 1e308, 1e308)
    monkeypatch.setattr(mesh_render, "_select_view_rotation", lambda *_args: np.eye(3))
    assert stl_fallback.render_stl_thumbnail(path) is None

    sampled.bounds_min = (0.0, 0.0, 0.0)
    sampled.bounds_max = (1.0, 1.0, 1.0)
    monkeypatch.setattr(
        mesh_render,
        "_select_view_rotation",
        lambda *_args: np.full((3, 3), np.nan),
    )
    assert stl_fallback.render_stl_thumbnail(path) is None


def test_stl_fallback_returns_none_when_optional_render_dependencies_are_missing(
    tmp_path: Path, monkeypatch
) -> None:
    import builtins

    from app.services import stl_fallback

    path = tmp_path / "missing-dependency.stl"
    _write_renderable_binary_stl(path, 1)
    original_import = builtins.__import__

    def missing_numpy(name, *args, **kwargs):
        if name == "numpy":
            raise ImportError("numpy unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_numpy)
    assert stl_fallback.render_stl_thumbnail(path) is None


def test_over_cap_3mf_still_gets_embedded_preview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
    png = _valid_preview_png()
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


def test_valid_embedded_3mf_preview_precedes_mesh_render(
    tmp_path: Path, monkeypatch
) -> None:
    """A valid slicer preview is preferred even when mesh loading is safe."""
    png = _valid_preview_png((220, 40, 120))
    p = tmp_path / "preview-first.3mf"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("3D/3dmodel.model", b"<mesh/>")
        zf.writestr("Metadata/thumbnail.png", png)

    monkeypatch.setattr(mesh_processing, "_load_mesh", lambda _p: _fake_mesh(10))
    monkeypatch.setattr(
        mesh_processing.mesh_render,
        "render_mesh_thumbnail",
        lambda *args, **kwargs: b"RENDERED-MESH",
    )

    _geometry, thumb = mesh_processing.analyze_mesh(p)
    assert thumb == png


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
    png = _valid_preview_png()
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
