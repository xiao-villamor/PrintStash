"""Sizing a mesh file without opening it, because opening it is the danger.

Loading and rasterising a mesh costs roughly 700 MB of peak RSS per million
triangles, and that cost is paid *inside* `trimesh.load_mesh` — by the time the
process knows the file was too big, it has already been OOM-killed. So the only
workable defence is to estimate the triangle count from the file's structure
first, and `_estimate_triangle_count` is that estimate (issue #24).

Which makes the direction of every error the thing that matters. An
**over**-estimate skips a mesh that would have rendered: a missing thumbnail,
recoverable, annoying. An **under**-estimate loads a mesh that kills the scan
and takes the whole library indexing pass with it. Every assertion here is
therefore about the estimate staying a safe *upper* bound, and the trap cases
are the ones that used to read low:

- A binary STL with exporter metadata appended breaks the exact `84 + 50N` size
  check. The old code then fell through to the ASCII estimate (`size // 250`),
  underestimating by about 5x.
- A binary STL whose 80-byte header text begins with `solid` — the classic STL
  trap — must stay on the binary path.

`None` means "cannot size this", which callers treat as "load carefully behind
the byte-size guard", not as "safe".
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

from app.services import mesh_processing

from .._meshes import _write_binary_stl, _write_obj


class TestEstimateTriangleCount:
    def test_binary_stl_triangle_count_is_exact(self, tmp_path: Path) -> None:
        p = tmp_path / "cube.stl"
        _write_binary_stl(p, 1234)
        assert mesh_processing._estimate_triangle_count(p) == 1234

    def test_binary_stl_with_trailing_bytes_is_not_underestimated(
        self, tmp_path: Path
    ) -> None:
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

    def test_binary_stl_header_starting_with_solid_is_not_misread_as_ascii(
        self,
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
        assert est >= n

    def test_an_ascii_stl_is_estimated_from_its_text_density(
        self, tmp_path: Path
    ) -> None:
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

    def test_3mf_triangle_count_from_uncompressed_xml(self, tmp_path: Path) -> None:
        p = tmp_path / "dense.3mf"
        model_xml = b"<triangle/>" * 10_000  # 110_000 bytes of "mesh"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("3D/3dmodel.model", model_xml)
        # ~70 bytes per triangle proxy.
        assert mesh_processing._estimate_triangle_count(p) == len(model_xml) // 70

    def test_3mf_without_model_part_falls_back_to_total_uncompressed_size(
        self,
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

    def test_ply_face_count_from_header(self, tmp_path: Path) -> None:
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

    def test_ply_without_face_element_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "points.ply"
        p.write_bytes(
            b"ply\nformat ascii 1.0\nelement vertex 3\n"
            b"property float x\nend_header\n0 0 0\n"
        )
        assert mesh_processing._estimate_triangle_count(p) is None

    def test_ply_header_without_end_header_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "truncated.ply"
        # File ends mid-header, before an "end_header" line is ever seen.
        p.write_bytes(b"ply\nformat ascii 1.0\nelement vertex 3\n")
        assert mesh_processing._estimate_triangle_count(p) is None

    def test_ply_face_count_non_integer_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "bad-count.ply"
        p.write_bytes(b"ply\nformat ascii 1.0\nelement face notanumber\nend_header\n")
        assert mesh_processing._estimate_triangle_count(p) is None

    def test_obj_triangle_count_from_face_directives(self, tmp_path: Path) -> None:
        p = tmp_path / "mesh.obj"
        _write_obj(p, tri_faces=300)
        # 300 triangular faces -> 300 triangles (exact for tris).
        assert mesh_processing._estimate_triangle_count(p) == 300

    def test_obj_ngon_faces_count_conservatively(self, tmp_path: Path) -> None:
        p = tmp_path / "quads.obj"
        _write_obj(p, tri_faces=10, quads=5)  # 10 + 5*(4-2) = 20 triangles
        assert mesh_processing._estimate_triangle_count(p) == 20

    def test_obj_without_faces_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "points.obj"
        p.write_bytes(b"v 0 0 0\nv 1 0 0\nvn 0 0 1\n")
        assert mesh_processing._estimate_triangle_count(p) is None

    def test_estimator_returns_none_for_unrecognised_suffix(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "part.step"
        p.write_bytes(b"not a real STEP file")
        assert mesh_processing._estimate_triangle_count(p) is None

    def test_estimator_returns_none_for_corrupt_3mf(self, tmp_path: Path) -> None:
        p = tmp_path / "corrupt.3mf"
        p.write_bytes(b"not actually a zip")
        assert mesh_processing._estimate_triangle_count(p) is None
