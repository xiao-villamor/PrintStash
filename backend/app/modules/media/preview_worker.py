"""One bounded secondary batch in a supervised native worker."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from printstash_core.mesh.similarity import GeometryError

from app.modules.media import mesh_policy
from app.modules.media.geometry_analysis import _load
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError, read_spec
from app.modules.media.preview_contracts import (
    MAX_FACES,
    MAX_SOURCE_BYTES,
    PreviewLimitError,
)
from app.modules.media.preview_views import render_views
from app.modules.media.stl_isolation import FileIdentity, manifest_for


def produce(
    path: Path,
    file_type: str,
    source_sha256: str,
    directory: Path,
    progress: Callable[[int, int], None] | None = None,
    *,
    recipe: int = 1,
) -> bytes:
    identity = FileIdentity.of(path.stat())
    if identity.size > MAX_SOURCE_BYTES:
        raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
    with path.open("rb") as source:
        if manifest_for(source).sha256 != source_sha256:
            raise MeshWorkerError(ThumbnailFailureReason.SOURCE_CHANGED)
    estimate = mesh_policy.estimate_triangle_count(path, file_type=file_type)
    if estimate is not None and estimate > MAX_FACES:
        raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
    with mesh_policy.render_admission():
        prepared = _load(path, file_type, triangle_cap=MAX_FACES, include_brep=False)
        # The loader may offer a sampled oversized STL for AI; galleries never do.
        from app.modules.media.mesh_facts import CompleteGeometry

        if not isinstance(prepared.geometry, CompleteGeometry):
            raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
        manifest, images = render_views(
            prepared.whole_mesh,
            source_sha256=source_sha256,
            progress=progress,
            recipe=recipe,
        )
    if FileIdentity.of(path.stat()) != identity:
        raise MeshWorkerError(ThumbnailFailureReason.SOURCE_CHANGED)
    with (directory / "views.bin").open("xb") as destination:
        for image in images:
            destination.write(image)
        destination.flush()
        os.fsync(destination.fileno())
    return manifest.model_dump_json().encode()


_RESOURCE_CODES = frozenset(
    {
        "geometry_work_limit",
        "resource_limit",
        "scene_resource_limit",
        "archive_resource_limit",
    }
)


# GeometryError is a ValueError. Anything else is a bug for the supervisor.
EXPECTED_FAILURES = (MeshWorkerError, PreviewLimitError, ImportError, ValueError)


def failure_reason(exc: Exception) -> ThumbnailFailureReason:
    """The closed reason reported for one of ``EXPECTED_FAILURES``."""
    if isinstance(exc, MeshWorkerError):
        return exc.reason
    if isinstance(exc, PreviewLimitError):
        return ThumbnailFailureReason.RESOURCE_LIMIT
    if isinstance(exc, GeometryError):
        if exc.code in _RESOURCE_CODES:
            return ThumbnailFailureReason.RESOURCE_LIMIT
        if exc.code in {"unsupported_geometry", "step_unavailable"}:
            return ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
        return ThumbnailFailureReason.INVALID_SOURCE
    if isinstance(exc, ImportError):
        return ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
    if isinstance(exc, ValueError):
        return ThumbnailFailureReason.INVALID_SOURCE
    raise TypeError(f"unexpected_preview_failure:{type(exc).__name__}") from exc


def main(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    spec = read_spec(argv)
    try:
        payload = produce(
            Path(spec["path"]),
            spec["file_type"],
            spec["expected_sha256"],
            Path(spec["output_directory"]),
            progress=lambda done, total: output.write(f"P:{done}:{total}\n".encode()),
            recipe=spec["recipe"],
        )
    except EXPECTED_FAILURES as exc:
        payload = b"FAIL" + failure_reason(exc).value.encode()
    output.write(payload)
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
