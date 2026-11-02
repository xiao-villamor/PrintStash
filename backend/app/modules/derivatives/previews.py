"""Publish a view set atomically as one immutable, range-addressed object.

The manifest is small catalog metadata; encoded WebPs are concatenated without
compression. Existing derivative ownership therefore covers every byte during
backup, migration and purge, while range-capable backends transfer one view.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from functools import partial

from pydantic import ValidationError

from app.core.cancellation import checkpoint
from app.core.time import utcnow
from app.db.models import DerivativeKind, DerivativeState, JobKind
from app.db.session import get_session_factory
from app.modules.media import mesh_isolation, thumbnail
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.native_budget import MeshSource, RasterCodec, RasterWork
from app.modules.media.preview_contracts import (
    HEIGHT,
    MAX_BUNDLE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_SOURCE_BYTES,
    WIDTH,
    PreviewManifest,
)
from app.modules.media.source_preparation import reserve_sources
from app.modules.media.stl_isolation import manifest_for, open_worker_output
from app.modules.storage.artifact_content import ArtifactContentError, resolve
from app.modules.storage.capacity import CapacityManager, CapacityResource
from app.modules.storage.capacity_estimates import vault_allocation
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import adopt_publication, prepare_stream
from app.modules.work.contracts import JobExecution
from app.runtime.native_admission import AdmissionTooLarge

from . import records
from .producers import Outcome, _announced, _begin, _fail


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
    if file.size_bytes > MAX_SOURCE_BYTES:
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
            prefix, done, total = line.decode("ascii").split(":")
            count, size = int(done), int(total)
            if prefix != "P" or not completed < count <= size <= 6:
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
            with mesh_isolation.prepared_worker_result(
                "app.modules.media.preview_worker",
                {
                    "path": str(source.absolute()),
                    "file_type": file.file_type.value,
                    "expected_sha256": file.sha256,
                    "recipe": attempt.recipe,
                },
                workspace=prepared.directory,
                sources=(MeshSource(source, file.file_type.value),),
                # Images are encoded sequentially; one live raster, six bounded WebPs.
                work=RasterWork(WIDTH, HEIGHT, 1, RasterCodec.WEBP),
                reply_limit=MAX_MANIFEST_BYTES,
                on_chunk=receive,
            ) as (reply, directory):
                payload = reply.payload.rsplit(b"\n", 1)[-1]
                if payload.startswith(b"FAIL"):
                    raise MeshWorkerError(
                        ThumbnailFailureReason(payload[4:].decode("ascii"))
                    )
                manifest = PreviewManifest.model_validate_json(payload)
                if (
                    manifest.source_sha256 != file.sha256
                    or manifest.recipe != attempt.recipe
                ):
                    raise ValueError("preview_source_changed")
                with open_worker_output(directory / "views.bin") as stream:
                    bundle = manifest_for(stream)
                    if bundle.size != manifest.size:
                        raise ValueError("preview_size_mismatch")
                    for view in manifest.views:
                        checkpoint(force=True)
                        image = stream.read(view.size)
                        if hashlib.sha256(image).hexdigest() != view.sha256:
                            raise ValueError("preview_digest_mismatch")
                        canonical = thumbnail.to_webp(
                            image,
                            width=WIDTH,
                            normalize=False,
                            preserve_frame=True,
                            renderer_encoded=True,
                        )
                        if canonical != image:
                            raise ValueError("preview_noncanonical_image")
                    stream.seek(0)
                    backend = get_backend()
                    key = backend.blob_key(
                        "_derivatives",
                        0,
                        f"{file.sha256}-previews-r{attempt.recipe}-{attempt.token}.bin",
                    )
                    with get_session_factory().scoped_session() as session:
                        candidate = prepare_stream(
                            session,
                            backend,
                            key,
                            stream,
                            expected_size=manifest.size,
                            sha256=bundle.sha256,
                            object_kind="previews",
                        )
                        records.mark_ready(
                            session,
                            attempt,
                            now=utcnow(),
                            storage_key=candidate.receipt.key,
                            output=manifest.model_dump(mode="json"),
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        session.flush()
                        adopt_publication(session, candidate)
                        session.commit()
    except records.AttemptSuperseded:
        return Outcome({})
    except (
        MeshWorkerError,
        AdmissionTooLarge,
        ArtifactContentError,
        ValueError,
        ValidationError,
        OSError,
        StorageCollisionError,
    ) as exc:
        reason = (
            exc.reason.value
            if isinstance(exc, MeshWorkerError)
            else "resource_limit"
            if isinstance(exc, AdmissionTooLarge)
            else "storage"
            if isinstance(exc, (OSError, StorageCollisionError))
            else "invalid_source"
        )
        if reason in {"unsupported_format", "unsupported_capability", "no_geometry"}:
            with get_session_factory().scoped_session() as session:
                records.mark_skipped(session, attempt, reason, now=utcnow())
                session.commit()
            return Outcome({DerivativeKind.PREVIEWS: DerivativeState.SKIPPED})
        _fail(
            attempt,
            reason,
            deterministic=reason not in {"storage", "worker_failed", "timeout"},
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return Outcome({DerivativeKind.PREVIEWS: DerivativeState.FAILED})
    return Outcome({DerivativeKind.PREVIEWS: DerivativeState.READY})


def derive(
    file_id: int,
    *,
    execution: JobExecution | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Outcome:
    return _announced(partial(_derive, progress=progress))(file_id, execution=execution)
