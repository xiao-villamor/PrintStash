"""One mesh-to-STL conversion in a disposable process; see `stl_isolation`.

The parent supplies the whole request as one JSON argument, including the file
the mesh is written to, and reads one small frame from stdout. Anything a native
loader prints must not corrupt that frame, so stdout is pointed at the null device
once the real pipe has been duplicated.
"""

from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from printstash_core.mesh.similarity import GeometryError

from app.core.cancellation import checkpoint
from app.modules.media import mesh_policy
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError, read_spec
from app.modules.media.mesh_loading import load_mesh, load_step_mesh
from app.modules.media.mesh_policy import canonical_suffix
from app.modules.media.mesh_resources import load_3mf
from app.modules.media.stl_isolation import (
    FAILURE_MAGIC,
    MAX_STL_BYTES,
    FileIdentity,
    STLManifest,
    encode_manifest,
    manifest_for,
)
from app.modules.media.three_mf_scene import Unsupported3MFCapability

if TYPE_CHECKING:
    from trimesh import Trimesh

_STL_VALIDATION_FACES = 4096


def _validate_float32_facets(mesh: Trimesh) -> None:
    """Reject float32 overflow or collapsed source facets without changing them.

    Index only a bounded face batch, rather than materializing mesh.triangles.
    Normalize source edges before crossing to preserve tiny nonzero areas;
    decoded float32 edges are crossed in float64 to avoid float32 overflow.
    """
    import numpy as np

    for start in range(0, len(mesh.faces), _STL_VALIDATION_FACES):
        checkpoint()
        triangles = np.asarray(
            mesh.vertices[mesh.faces[start : start + _STL_VALIDATION_FACES]],
            dtype=np.float64,
        )
        if not np.isfinite(triangles).all():
            raise MeshWorkerError(ThumbnailFailureReason.INVALID_SOURCE)
        with np.errstate(over="ignore", invalid="ignore"):
            packed = triangles.astype(np.float32)
        if not np.isfinite(packed).all():
            raise MeshWorkerError(ThumbnailFailureReason.INVALID_SOURCE)

        original_edges = triangles[:, 1:] - triangles[:, :1]
        scale = np.max(np.abs(original_edges), axis=2, keepdims=True)
        normalized_edges = np.divide(
            original_edges,
            scale,
            out=np.zeros_like(original_edges),
            where=scale != 0,
        )
        original_area = np.any(
            np.cross(normalized_edges[:, 0], normalized_edges[:, 1]) != 0, axis=1
        )
        decoded = packed.astype(np.float64)
        packed_edges = decoded[:, 1:] - decoded[:, :1]
        packed_area = np.any(
            np.cross(packed_edges[:, 0], packed_edges[:, 1]) != 0, axis=1
        )
        if np.any(original_area & ~packed_area):
            raise MeshWorkerError(ThumbnailFailureReason.INVALID_SOURCE)


@dataclass
class _CappedSTLOutput(io.IOBase):
    stream: BinaryIO
    written: int = 0

    def write(self, value: bytes) -> int:
        if self.written + len(value) > MAX_STL_BYTES:
            raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
        amount = self.stream.write(value)
        self.written += amount
        return amount

    def flush(self) -> None:
        if not self.stream.closed:
            self.stream.flush()


def convert(path: Path, file_type: str | None, destination: Path) -> STLManifest:
    """Preserve resource refusal reasons instead of collapsing them into no output."""
    import trimesh

    file_type = canonical_suffix(path, file_type).lstrip(".")
    if mesh_policy.exceeds_cap(path, file_type=file_type):
        raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
    with mesh_policy.render_admission():
        try:
            mesh = (
                load_3mf(
                    path, max_faces=mesh_policy.load_face_budget(".3mf")
                ).whole_mesh
                if file_type == "3mf"
                else load_step_mesh(path, strict_failures=True)
                if file_type in {"step", "stp"}
                else load_mesh(path, file_type=file_type)
            )
        except GeometryError as exc:
            reason = (
                ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
                if isinstance(exc, Unsupported3MFCapability)
                else ThumbnailFailureReason.RESOURCE_LIMIT
                if exc.code
                in {
                    "archive_resource_limit",
                    "resource_limit",
                    "scene_resource_limit",
                    "geometry_work_limit",
                    "worker_oom",
                }
                else ThumbnailFailureReason.TIMEOUT
                if exc.code == "tessellation_timeout"
                else ThumbnailFailureReason.UNSUPPORTED_FORMAT
                if exc.code == "step_unavailable"
                else ThumbnailFailureReason.INVALID_SOURCE
            )
            raise MeshWorkerError(reason) from exc
        if not isinstance(mesh, trimesh.Trimesh):
            raise MeshWorkerError(ThumbnailFailureReason.INVALID_SOURCE)
        if len(mesh.faces) == 0:
            raise MeshWorkerError(ThumbnailFailureReason.NO_GEOMETRY)
        # Binary STL's exact expansion is known before its exporter allocates.
        if 84 + 50 * len(mesh.faces) > MAX_STL_BYTES:
            raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
        _validate_float32_facets(mesh)
        with destination.open("xb") as stream:
            mesh.export(file_obj=_CappedSTLOutput(stream), file_type="stl")
            stream.flush()
            os.fsync(stream.fileno())
        with destination.open("rb") as stream:
            return manifest_for(stream)


def main(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    spec = read_spec(argv)
    try:
        path = Path(spec["path"])
        try:
            identity = FileIdentity.of(path.stat())
            with path.open("rb") as source:
                source_manifest = manifest_for(source)
        except OSError as exc:
            raise MeshWorkerError(ThumbnailFailureReason.STORAGE) from exc
        if source_manifest.sha256 != spec["expected_sha256"]:
            raise MeshWorkerError(ThumbnailFailureReason.SOURCE_CHANGED)
        destination = Path(spec["output_directory"]) / "mesh.stl"
        converted = convert(path, spec["file_type"], destination)
        try:
            unchanged = FileIdentity.of(path.stat()) == identity
        except OSError as exc:
            raise MeshWorkerError(ThumbnailFailureReason.STORAGE) from exc
        if not unchanged:
            raise MeshWorkerError(ThumbnailFailureReason.SOURCE_CHANGED)
        frame = encode_manifest(converted)
    except MeshWorkerError as exc:
        frame = FAILURE_MAGIC + exc.reason.value.encode("ascii")
    output.write(frame)
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
