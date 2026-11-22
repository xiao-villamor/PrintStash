"""Publish a view set atomically as one immutable, range-addressed object.

The manifest is small catalog metadata; encoded WebPs are concatenated without
compression. Existing derivative ownership therefore covers every byte during
backup, migration and purge, while range-capable backends transfer one view.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict
from functools import partial

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.db.models import (
    DerivativeKind,
    DerivativeState,
    FileType,
    JobKind,
    SystemConfig,
)
from app.db.session import get_session_factory
from app.modules.media import mesh_isolation
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.native_budget import MeshSource, RasterCodec, RasterWork
from app.modules.media.preview_contracts import (
    GALLERY_WIDTH,
    MAX_BUNDLE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_VIEWS,
    PreviewManifest,
    height_for,
)
from app.modules.media.source_preparation import reserve_sources
from app.modules.media.stl_isolation import (
    manifest_for,
    open_worker_output,
)
from app.modules.storage.artifact_content import ArtifactContentError, resolve
from app.modules.storage.capacity import CapacityManager, CapacityResource
from app.modules.storage.capacity_estimates import vault_allocation
from app.modules.work.contracts import JobExecution
from app.runtime.native_admission import AdmissionTooLarge

from . import policy, records
from . import preview_publication as publication
from .producers import (
    Outcome,
    _announced,
    _begin,
    _fail,
    _skip_meshless,
    _sliced_archive,
)


def _derive(
    file_id: int,
    *,
    execution: JobExecution | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Outcome:
    begun = _begin(file_id, JobKind.DERIVATIVES_PREVIEWS, execution=execution)
    if begun is None or DerivativeKind.PREVIEWS not in begun[1]:
        return Outcome({})
    file, attempts = begun
    attempt = attempts[DerivativeKind.PREVIEWS]
    with get_session_factory().scoped_session() as session:
        include_poles = policy.preview_poles(session)
        planar_cover = publication.planar_cover_rotation(session, file)
        appearance = policy.render_appearance(session.get(SystemConfig, 1))
    width = GALLERY_WIDTH
    # VAULT_MESH_MAX_LOAD_MB: the same source-size ceiling as every mesh path.
    if file.size_bytes > settings.mesh_max_load_mb * 1024 * 1024:
        _fail(attempt, "resource_limit", deterministic=True)
        return Outcome({DerivativeKind.PREVIEWS: DerivativeState.FAILED})
    started = time.monotonic()
    buffered = bytearray()
    completed = 0

    def receive(chunk: bytes) -> None:
        nonlocal completed
        buffered.extend(chunk)
        while b"\n" in buffered:
            line, _, remainder = buffered.partition(b"\n")
            buffered[:] = remainder
            # A garbled stream is the worker's fault, never the Artifact's:
            # it must stay retryable rather than become a deterministic failure.
            try:
                prefix, done, total = line.decode("ascii").split(":")
                count, size = int(done), int(total)
            except ValueError as exc:  # UnicodeDecodeError is a ValueError
                raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc
            if prefix != "P" or not completed < count <= size <= MAX_VIEWS:
                raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
            completed = count
            checkpoint(force=True)
            if progress is not None:
                progress(count, size)

    try:
        with (
            reserve_sources(
                (resolve(file),), output_bytes=MAX_BUNDLE_BYTES
            ) as prepared,
            CapacityManager(get_session_factory()).hold(
                f"derive:previews:{file_id}:{attempt.token}",
                [
                    CapacityResource.for_path(
                        prepared.directory,
                        file.size_bytes * 2 + MAX_BUNDLE_BYTES,
                        role="preview preparation",
                    ),
                    vault_allocation(MAX_BUNDLE_BYTES, role="preview publication"),
                ],
                transient=True,
            ),
            prepared.materialize(capacity_claimed=True) as sources,
        ):
            (source,) = sources
            if (
                file.file_type is FileType.THREE_MF
                and _sliced_archive(source) is not None
            ):
                return Outcome({DerivativeKind.PREVIEWS: _skip_meshless(attempt)})
            with mesh_isolation.prepared_worker_result(
                "app.modules.media.preview_worker",
                {
                    "path": str(source.absolute()),
                    "file_type": file.file_type.value,
                    "expected_sha256": file.sha256,
                    "recipe": attempt.recipe,
                    "include_poles": include_poles,
                    "width": width,
                    "planar_cover": planar_cover,
                    "preview_appearance": asdict(appearance),
                },
                workspace=prepared.directory,
                sources=(MeshSource(source, file.file_type.value),),
                # Images are encoded sequentially; one live raster, six bounded WebPs.
                work=RasterWork(width, height_for(width), 1, RasterCodec.WEBP),
                reply_limit=MAX_MANIFEST_BYTES,
                on_chunk=receive,
            ) as (reply, directory):
                payload = reply.payload.rsplit(b"\n", 1)[-1]
                if payload.startswith(b"FAIL"):
                    raise MeshWorkerError(
                        ThumbnailFailureReason(payload[4:].decode("ascii"))
                    )
                manifest = PreviewManifest.model_validate_json(payload)
                with open_worker_output(directory / "views.bin") as stream:
                    publication.publish(
                        file,
                        attempt,
                        manifest,
                        stream,
                        manifest_for(stream),
                        started=started,
                    )
    except records.AttemptSuperseded:
        return Outcome({})
    except (
        MeshWorkerError,
        AdmissionTooLarge,
        ArtifactContentError,
        *publication.SOURCE_FAILURES,
        *publication.STORAGE_FAILURES,
    ) as exc:
        state = publication.settle_failure(
            attempt,
            publication.reason_for(exc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return Outcome({DerivativeKind.PREVIEWS: state})
    return Outcome({DerivativeKind.PREVIEWS: DerivativeState.READY})


def derive(
    file_id: int,
    *,
    execution: JobExecution | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Outcome:
    return _announced(partial(_derive, progress=progress))(file_id, execution=execution)
