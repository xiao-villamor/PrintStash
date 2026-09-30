"""Convert a mesh to STL in a disposable worker process.

The 3D viewer converts a 3MF or OBJ to STL on demand, inside a request, from a
file a user uploaded: the one place a person can make the server parse a mesh at
will. In the API process an out-of-memory kill there takes every request with it
(#259). The parent supervises a child exactly as it does for mesh derivatives
(`mesh_isolation`), so a file that exhausts memory or time costs one request.

The converted mesh is written to a file the parent owns, because an STL of a large
model is far bigger than a reply frame should be; the reply says how many bytes
were written and the parent checks the file against it.
"""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from app.modules.media import mesh_isolation, mesh_processing
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.thumbnail_engine import ThumbnailFailureReason

SIZE_MAGIC = b"STL1"
NOTHING_MAGIC = b"NONE"
# Binary STL is 50 bytes a face plus an 84-byte header. A worker admitted to the
# analysis ceiling of 2,000,000 faces writes about 100 MB, so this is generous.
MAX_STL_BYTES = 256 * 1024 * 1024


def encode_reply(size: int | None) -> bytes:
    if size is None:
        return NOTHING_MAGIC
    return SIZE_MAGIC + struct.pack("!Q", size)


def decode_reply(payload: bytes) -> int | None:
    """The number of bytes written, or None when there was nothing to convert."""
    if payload == NOTHING_MAGIC:
        return None
    if len(payload) == len(SIZE_MAGIC) + 8 and payload.startswith(SIZE_MAGIC):
        return struct.unpack("!Q", payload[len(SIZE_MAGIC) :])[0]
    raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)


def to_stl_bytes(path: Path, *, file_type: str | None = None) -> bytes | None:
    """`mesh_processing.to_stl_bytes`, run in a supervised child.

    Returns None when the mesh cannot be converted (unreadable, or over budget);
    raises `MeshWorkerError` when the worker was killed, timed out or died.
    """
    if mesh_processing._canonical_suffix(path, file_type) == ".stl":
        # Already STL: the bytes are returned untouched and nothing is parsed.
        try:
            if path.stat().st_size > MAX_STL_BYTES:
                raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
            return path.read_bytes()
        except OSError:
            return None
    with tempfile.TemporaryDirectory(prefix="printstash-stl-") as directory:
        output = Path(directory) / "mesh.stl"
        spec = {
            "path": mesh_isolation.absolute(path),
            "file_type": file_type,
            "output": str(output),
        }
        size = decode_reply(
            mesh_isolation.run_worker("app.modules.media.stl_worker", spec)
        )
        if size is None:
            return None
        try:
            written = output.stat().st_size
        except OSError as exc:
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc
        if written != size or size > MAX_STL_BYTES:
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
        return output.read_bytes()
