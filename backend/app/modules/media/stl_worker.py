"""One mesh-to-STL conversion in a disposable process; see `stl_isolation`.

The parent supplies the whole request as one JSON argument, including the file
the mesh is written to, and reads one small frame from stdout. Anything a native
loader prints must not corrupt that frame, so stdout is pointed at the null device
once the real pipe has been duplicated.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from printstash_core.mesh.similarity import GeometryError
from trimesh.exchange.stl import export_stl

from app.modules.media import mesh_policy
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError, read_spec
from app.modules.media.mesh_loading import load_step_mesh, to_stl_bytes
from app.modules.media.mesh_resources import load_3mf
from app.modules.media.stl_isolation import FAILURE_MAGIC, encode_reply
from app.modules.media.three_mf_scene import Unsupported3MFCapability


def convert(path: Path, file_type: str | None) -> bytes | None:
    """Preserve resource refusal reasons instead of collapsing them into no output."""
    file_type = mesh_policy.canonical_suffix(path, file_type).lstrip(".")
    if mesh_policy.exceeds_cap(path, file_type=file_type):
        raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
    if file_type not in {"3mf", "step", "stp"}:
        return to_stl_bytes(path, file_type=file_type)
    with mesh_policy.render_admission():
        try:
            mesh = (
                load_3mf(
                    path, max_faces=mesh_policy.load_face_budget(".3mf")
                ).whole_mesh
                if file_type == "3mf"
                else load_step_mesh(path, strict_failures=True)
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
        return None if mesh is None else export_stl(mesh)


def main(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    spec = read_spec(argv)
    try:
        converted = convert(Path(spec["path"]), spec["file_type"])
        if converted is None:
            frame = encode_reply(None)
        else:
            Path(spec["output"]).write_bytes(converted)
            frame = encode_reply(len(converted))
    except MeshWorkerError as exc:
        frame = FAILURE_MAGIC + exc.reason.value.encode("ascii")
    output.write(frame)
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
