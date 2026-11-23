"""Verify and publish a view set, and settle the views a cover pass owns.

Both producers of multi-angle views end here: the batch (``previews``) and the
cover pass (``producers``), which renders the views from the mesh it already
loaded. This module depends on neither, so neither depends on the other.
"""

from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass
from typing import BinaryIO

from pydantic import ValidationError
from sqlmodel import Session, col, select

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    File,
    JobKind,
)
from app.db.session import get_session_factory
from app.modules.media import thumbnail
from app.modules.media.mesh_contracts import ViewsRequest
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.mesh_protocol import ViewsReady, ViewsRefused
from app.modules.media.preview_contracts import (
    GALLERY_WIDTH,
    STRIP_WIDTH,
    Matrix,
    PreviewManifest,
    ViewProfile,
    ViewRole,
)
from app.modules.media.stl_isolation import STLManifest
from app.modules.storage.capacity import CapacityManager
from app.modules.storage.capacity_estimates import vault_allocation
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import adopt_publication, prepare_stream
from app.modules.work.contracts import JobExecution
from app.runtime.native_admission import AdmissionTooLarge

from . import policy, records
from .kinds import PREVIEWS_RECIPE

STORAGE_FAILURES = (OSError, StorageCollisionError)
SOURCE_FAILURES = (ValueError, ValidationError)


def publish(
    file: File,
    attempt: records.DerivativeAttempt,
    manifest: PreviewManifest,
    stream: BinaryIO,
    bundle: STLManifest,
    *,
    started: float,
) -> None:
    """Verify every advertised view, then publish the bundle under ``attempt``.

    ``bundle`` is the size and digest of ``stream``'s bytes, taken by the
    caller from whatever holds them: a worker's output file or memory.
    """
    if manifest.source_sha256 != file.sha256 or manifest.recipe != attempt.recipe:
        raise ValueError("preview_source_changed")
    if bundle.size != manifest.size:
        raise ValueError("preview_size_mismatch")
    for view in manifest.views:
        checkpoint(force=True)
        image = stream.read(view.size)
        if hashlib.sha256(image).hexdigest() != view.sha256:
            raise ValueError("preview_digest_mismatch")
        canonical = thumbnail.to_webp(
            image,
            width=view.width,
            normalize=False,
            preserve_frame=True,
            renderer_encoded=True,
        )
        if canonical != image:
            raise ValueError("preview_noncanonical_image")
        if view.thumbnail is not None:
            strip = stream.read(view.thumbnail.size)
            if hashlib.sha256(strip).hexdigest() != view.thumbnail.sha256:
                raise ValueError("preview_thumbnail_digest_mismatch")
            if (
                thumbnail.to_webp(
                    strip, width=STRIP_WIDTH, preserve_frame=True, renderer_encoded=True
                )
                != strip
            ):
                raise ValueError("preview_noncanonical_thumbnail")
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


def settle_failure(
    attempt: records.DerivativeAttempt, reason: str, *, duration_ms: int
) -> DerivativeState:
    """Record a closed failure exactly as the batch always has."""
    if reason in {"unsupported_format", "unsupported_capability", "no_geometry"}:
        with get_session_factory().scoped_session() as session:
            records.mark_skipped(session, attempt, reason, now=utcnow())
            session.commit()
        return DerivativeState.SKIPPED
    with get_session_factory().scoped_session() as session:
        records.mark_failed(
            session,
            attempt,
            reason,
            now=utcnow(),
            deterministic=reason not in {"storage", "worker_failed", "timeout"},
            duration_ms=duration_ms,
        )
        session.commit()
    return DerivativeState.FAILED


def reason_for(exc: Exception) -> str:
    return (
        exc.reason.value
        if isinstance(exc, MeshWorkerError)
        else "resource_limit"
        if isinstance(exc, AdmissionTooLarge)
        else "storage"
        if isinstance(exc, STORAGE_FAILURES)
        else "invalid_source"
    )


def planar_cover_rotation(session: Session, file: File) -> Matrix | None:
    """Preserve the camera of a published planar cover during regeneration."""
    rows = session.exec(
        select(ArtifactDerivative.output_json)
        .where(
            col(ArtifactDerivative.file_id) == file.id,
            col(ArtifactDerivative.kind) == DerivativeKind.PREVIEWS,
            col(ArtifactDerivative.storage_key).is_not(None),
        )
        .order_by(col(ArtifactDerivative.recipe_version).desc())
    )
    for data in rows:
        manifest = PreviewManifest.model_validate_json(data)
        if (
            manifest.source_sha256 == file.sha256
            and manifest.profile is ViewProfile.PLANAR
        ):
            face = next(
                (view for view in manifest.views if view.role is ViewRole.FACE), None
            )
            if face is not None:
                return face.rotation
    return None


@dataclass(frozen=True)
class PassClaim:
    """Views a cover pass owns: its attempt, its request, and what it replaced."""

    attempt: records.DerivativeAttempt
    request: ViewsRequest
    prior: records.PriorClaim | None


def claim_for_pass(
    session: Session, file: File, *, execution: JobExecution | None
) -> PassClaim | None:
    """Claim this Artifact's views for a cover pass that is about to load it.

    Runs inside the cover Job's claim transaction. A RUNNING row is satisfied
    work, so the batch never starts a second load for the same views.
    """
    if not policy.resolve(session)[JobKind.DERIVATIVES_PREVIEWS].enabled:
        return None
    if file.size_bytes > settings.mesh_max_load_mb * 1024 * 1024:
        return None
    kinds = {DerivativeKind.PREVIEWS: PREVIEWS_RECIPE}
    assert file.id is not None
    if not records.needed(session, file, kinds, now=utcnow()) and not stranded(
        session, file.id
    ):
        return None
    row, prior = records.begin_reversible(
        session, file, DerivativeKind.PREVIEWS, PREVIEWS_RECIPE, now=utcnow()
    )
    attempt = records.attempt(session, file, row, execution=execution)
    return PassClaim(
        attempt,
        ViewsRequest(
            recipe=attempt.recipe,
            include_poles=policy.preview_poles(session),
            source_sha256=file.sha256,
            width=GALLERY_WIDTH,
            planar_cover=planar_cover_rotation(session, file),
        ),
        prior,
    )


def stranded(session: Session, file_id: int) -> bool:
    """A RUNNING views row no batch Job owns: a dead cover pass claimed it.

    Only the batch and the subject's one active cover Job claim views, so the
    cover Job that asks (a replay or a retry) is that pass's successor.
    """
    from sqlmodel import col, select

    from app.db.models import ArtifactDerivative, Job
    from app.modules.work.jobs import active_job_predicate

    from .source import subject_key

    running = session.exec(
        select(ArtifactDerivative.id).where(
            ArtifactDerivative.file_id == file_id,
            ArtifactDerivative.kind == DerivativeKind.PREVIEWS,
            ArtifactDerivative.recipe_version == PREVIEWS_RECIPE,
            ArtifactDerivative.state == DerivativeState.RUNNING,
        )
    ).first()
    if running is None:
        return False
    return (
        session.exec(
            select(Job.id).where(
                col(Job.kind) == JobKind.DERIVATIVES_PREVIEWS,
                col(Job.subject_key) == subject_key(file_id),
                active_job_predicate(),
            )
        ).first()
        is None
    )


def settle_stranded(session: Session, file_id: int, reason: str) -> None:
    """The cover Job ended for good: its stranded views become a retry."""
    if stranded(session, file_id):
        records.fail_in_flight(
            session,
            file_id,
            reason,
            now=utcnow(),
            kinds={DerivativeKind.PREVIEWS: PREVIEWS_RECIPE},
        )


def release_pass(claim: PassClaim) -> None:
    """Give views back to the batch when the cover pass ended without them."""
    try:
        with get_session_factory().scoped_session() as session:
            records.release(session, claim.attempt, claim.prior)
            session.commit()
    except records.AttemptSuperseded:
        pass


def publish_pass(
    file: File,
    attempt: records.DerivativeAttempt,
    output: ViewsReady | ViewsRefused,
    *,
    started: float,
) -> DerivativeState | None:
    """Settle views the cover pass produced; None when another owner took over."""
    try:
        if isinstance(output, ViewsRefused):
            return settle_failure(
                attempt, output.reason.value, duration_ms=output.duration_ms
            )
        with CapacityManager(get_session_factory()).hold(
            f"derive:previews:{file.id}:{attempt.token}",
            [vault_allocation(output.manifest.size, role="preview publication")],
            transient=True,
        ):
            publish(
                file,
                attempt,
                output.manifest,
                io.BytesIO(output.bundle),
                STLManifest(
                    len(output.bundle), hashlib.sha256(output.bundle).hexdigest()
                ),
                started=started,
            )
        return DerivativeState.READY
    except records.AttemptSuperseded:
        return None
    except (*SOURCE_FAILURES, *STORAGE_FAILURES) as exc:
        try:
            return settle_failure(
                attempt,
                reason_for(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except records.AttemptSuperseded:
            return None


def fail_pass(
    attempt: records.DerivativeAttempt, reason: str
) -> DerivativeState | None:
    """The cover pass ended before its views frame; settle them like the batch."""
    try:
        return settle_failure(attempt, reason, duration_ms=0)
    except records.AttemptSuperseded:
        return None
