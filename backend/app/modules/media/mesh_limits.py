"""Mesh sizing and host limits, independent of loading and admission."""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional


def _canonical_suffix(path: Path, file_type: str | None = None) -> str:
    """Return the source suffix even when *path* is an FD-backed alias.

    External-library scans deliberately read through ``/proc/self/fd`` so a
    mount replacement cannot change the bytes being processed.  Those aliases
    have no filename suffix, so callers that know the catalogued type pass it
    explicitly here.
    """
    if file_type is None:
        return path.suffix.lower()
    suffix = str(file_type).lower()
    return suffix if suffix.startswith(".") else f".{suffix}"


def _estimate_triangle_count(
    path: Path, *, file_type: str | None = None
) -> Optional[int]:
    """Best-effort triangle count *without* loading the mesh into memory.

    Loading is itself the memory blow-up (trimesh.load_mesh of a 5M-triangle mesh
    peaks at ~3.5 GB), so the only way to keep a dense lattice/gyroid model from
    OOM-killing the process is to estimate before we load and bail out (#24).

    Exact for binary STL (the triangle count is a uint32 in the header) and for
    PLY (the face count is declared in the ASCII header); a face-directive count
    for OBJ; a size-based estimate for ASCII STL and 3MF (uncompressed mesh XML).
    For an STL that fails the exact binary size check we distinguish ASCII from a
    binary file with trailing bytes and pick the *conservative* density, so we
    never underestimate a binary mesh into an unsafe load. Returns None for
    formats we can't cheaply size up (incl. STEP, which trimesh can't mesh
    without optional CAD deps anyway) — the caller then relies on the post-load
    cap, which still skips the render.
    """
    suffix = _canonical_suffix(path, file_type)
    try:
        if suffix == ".stl":
            size = path.stat().st_size
            with path.open("rb") as fh:
                sample = fh.read(1024)
            if len(sample) >= 84:
                count = struct.unpack("<I", sample[80:84])[0]
                # Binary STL is exactly 84 + 50 bytes per triangle; if the math
                # checks out we trust the header count exactly.
                if size == 84 + count * 50:
                    return count
            # The exact binary check failed. Now disambiguate a true ASCII STL
            # from a binary STL with trailing bytes (which also fails the check).
            # Guessing wrong toward ASCII is dangerous: ASCII is ~250 B/triangle
            # but binary is only ~50 B/triangle, so an ASCII estimate of a binary
            # file underestimates 5x and can let an over-cap mesh slip through to
            # the exact OOM load #24 set out to prevent. An ASCII STL starts with
            # the text "solid" and contains no NUL bytes; binary headers do.
            looks_ascii = (
                sample[:6].lower().startswith(b"solid") and b"\x00" not in sample
            )
            if looks_ascii:
                # ASCII STL: ~7 lines / ~250 bytes per triangle.
                return size // 250
            # Binary STL body is exactly 50 bytes per facet after the 84-byte
            # header; this stays a safe upper bound even with trailing bytes.
            return max(size - 84, 0) // 50
        if suffix == ".ply":
            # The PLY header is ASCII even when the body is binary, and it
            # declares the face count up front ("element face N"), so we can size
            # the mesh without parsing the (possibly huge) body.
            with path.open("rb") as fh:
                for _ in range(256):  # headers are short; bound the scan
                    line = fh.readline()
                    if not line:
                        break
                    parts = line.split()
                    if (
                        len(parts) >= 3
                        and parts[0].lower() == b"element"
                        and parts[1].lower() == b"face"
                    ):
                        try:
                            return int(parts[2])
                        except ValueError:
                            return None
                    if parts and parts[0].lower() == b"end_header":
                        break
            return None
        if suffix == ".obj":
            # OBJ is plain text; each "f " line is one face. trimesh triangulates
            # an n-gon face into (n - 2) triangles, so summing that keeps the
            # estimate a conservative upper bound (tris/quads dominate real files,
            # where it's already exact). A full text scan is cheap — no float
            # parsing, no mesh build — versus the trimesh.load_mesh it guards against.
            faces = 0
            with path.open("rb") as fh:
                for line in fh:
                    if not line.startswith(b"f ") and not line.startswith(b"f\t"):
                        continue
                    # vertex refs on the line, minus 2 = triangles after fan
                    # triangulation; clamp at 1 so a malformed face never
                    # subtracts from the count.
                    verts = len(line.split()) - 1
                    faces += max(verts - 2, 1)
            return faces or None
        if suffix == ".3mf":
            with zipfile.ZipFile(path) as zf:
                infos = zf.infolist()
                xml_bytes = sum(
                    info.file_size
                    for info in infos
                    if info.filename.lower().endswith(".model")
                )
                if not xml_bytes:
                    # Some 3MF variants keep the mesh outside a ".model" part (or
                    # name it unusually). Rather than return None and let the
                    # caller load a possibly-huge archive blind (#29), fall back to
                    # the total uncompressed payload as a conservative upper bound.
                    xml_bytes = sum(info.file_size for info in infos)
            # 3MF mesh XML runs ~70 bytes per <triangle> (verts are shared).
            return xml_bytes // 70 if xml_bytes else None
    except (OSError, zipfile.BadZipFile, struct.error):
        return None
    return None


# Measured peak RSS per triangle for a full load + thumbnail render, rounded up
# for safety margin. 3MF's XML loader plus the crease-aware rasteriser cost far
# more than a raw STL of the same geometry (~4.5x), so it gets its own factor.
_PEAK_BYTES_PER_TRIANGLE: dict[str, int] = {".3mf": 3600}
_DEFAULT_PEAK_BYTES_PER_TRIANGLE = 2200  # stl / ply / obj


def _detect_memory_limit_bytes() -> int | None:
    """Best-effort bytes of RAM the process may use before being OOM-killed.

    Container-aware: a Docker/NAS deployment is usually capped well below host
    RAM by its cgroup, and that limit — not the host's total — is what the kernel
    enforces. Takes the smallest of the cgroup limit (v2 then v1) and host
    ``MemTotal`` so the RAM-aware cap reflects the real ceiling. Returns None when
    nothing can be read (non-Linux, locked-down /proc), disabling the RAM cap.
    """
    limits: list[int] = []
    try:  # cgroup v2
        raw = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if raw != "max":
            limits.append(int(raw))
    except (OSError, ValueError):
        pass
    # On a host service the cgroup filesystem is mounted above this process's
    # group. Reading only its root misses MemoryMax on the service or a parent
    # slice. Containers with a cgroup namespace already expose their group at /.
    try:
        root = Path("/sys/fs/cgroup")
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if not line.startswith("0::/"):
                continue
            parts = PurePosixPath(line[3:]).parts[1:]
            if len(parts) > 128 or any(part in (".", "..") for part in parts):
                continue
            group = root.joinpath(*parts)
            while group != root:
                try:
                    value = int((group / "memory.max").read_text().strip())
                    if value > 0:
                        limits.append(value)
                except (OSError, ValueError):
                    pass
                group = group.parent
    except OSError:
        pass
    try:  # cgroup v1
        v1 = int(
            Path("/sys/fs/cgroup/memory/memory.limit_in_bytes").read_text().strip()
        )
        if 0 < v1 < (1 << 62):  # v1 uses a huge sentinel for "unlimited"
            limits.append(v1)
    except (OSError, ValueError):
        pass
    try:  # host total
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                limits.append(int(line.split()[1]) * 1024)
                break
    except (OSError, ValueError, IndexError):
        pass
    return min(limits) if limits else None
