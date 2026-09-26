"""Verified upload handoff: the ``ingestion.artifact_upload`` Job.

Finalizing an upload session claims it (``INGESTING``), records a queued Job
that owns the verified staged object through a staging lease, and nudges. This
Job commits the object as an Artifact (or attaches it as a revision) and
mirrors its terminal result onto the upload session.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import Session, select

from app.core.metrics import record_artifact_upload_event
from app.db.models import (
    SUFFIX_TO_FILE_TYPE,
    ArtifactUploadSession,
    ArtifactUploadState,
    FileRevisionStatus,
    FileType,
    JobKind,
    Model,
    StagingLease,
)
from app.db.scopes import live
from app.db.session import SessionFactory, get_session_factory
from app.modules.ingestion.ingestion import (
    StagedArtifact,
    add_gcode_revision_to_model,
    ingest_staged_file,
)
from app.modules.work.contracts import JobOutcome
from app.modules.work.jobs import jobs as registry

from .manager import SqlArtifactUploadManager
from .native_parts import NativeMultipartUploadAdapter


def subject_key(upload_id: str) -> str:
    return f"artifact_upload/{upload_id}"


def _staged_path(session: Session, job_id: str) -> Path | None:
    lease = session.exec(
        select(StagingLease).where(StagingLease.job_id == job_id)
    ).first()
    return Path(lease.path) if lease is not None else None


def run_verified_upload_ingestion(
    *,
    upload_id: str,
    job_id: str,
    session_factory: SessionFactory,
) -> None:
    """Run the commit for one verified upload, then mirror its terminal result."""

    with session_factory.scoped_session() as session:
        upload = session.get(ArtifactUploadSession, upload_id)
        if upload is None or upload.state != ArtifactUploadState.INGESTING:
            return
        options = json.loads(upload.request_json)
        purpose = upload.purpose
        filename = upload.filename
        owner_user_id = upload.owner_user_id
        target_id = upload.target_id
        staged_path = _staged_path(session, job_id)
        # Native uploads already have verified bytes in the store. Preserve
        # their identity through the Job so publication can copy them there.
        staged_origin = (
            NativeMultipartUploadAdapter.staged_origin(upload)
            if upload.adapter_id == NativeMultipartUploadAdapter.adapter_id
            else None
        )

    if staged_path is None or not staged_path.exists():
        registry.finish(
            job_id, JobOutcome.FAILED, error="staging_expired", retryable=False
        )
    elif purpose == "revision":
        registry.update(job_id, label="Attaching revision")
        try:
            with session_factory.scoped_session() as session:
                model = session.exec(
                    select(Model).where(Model.id == int(target_id or ""), live(Model))
                ).first()
                if model is None:
                    raise ValueError("model_not_found")
                file_row = add_gcode_revision_to_model(
                    session=session,
                    model=model,
                    staged_path=staged_path,
                    staged_origin=staged_origin,
                    original_filename=filename,
                    revision_label=options.get("revision_label"),
                    revision_status=FileRevisionStatus(
                        options.get("revision_status") or FileRevisionStatus.NEEDS_TEST
                    ),
                    revision_notes=options.get("revision_notes"),
                    is_recommended=bool(options.get("is_recommended", False)),
                )
                registry.finish(
                    job_id,
                    JobOutcome.COMPLETED,
                    model_id=model.id,
                    file_id=file_row.id,
                )
        except Exception:
            registry.finish(
                job_id,
                JobOutcome.FAILED,
                error="artifact_revision_ingestion_failed",
                retryable=True,
            )
    else:
        suffix = Path(filename).suffix.lower()
        file_type: FileType | None = None
        if purpose in {"gcode", "slicer"}:
            file_type = FileType.GCODE
        elif purpose in {"model", "external_writeback"}:
            file_type = SUFFIX_TO_FILE_TYPE.get(suffix)
        if file_type is None:
            registry.finish(
                job_id,
                JobOutcome.FAILED,
                error="artifact_upload_purpose_not_supported",
                retryable=False,
            )
        else:
            ingest_staged_file(
                job_id=job_id,
                artifact=StagedArtifact(
                    staged_path=staged_path,
                    original_filename=filename,
                    model_name=str(options.get("model_name") or Path(filename).stem),
                    file_type=file_type,
                    collection=options.get("collection"),
                    tags=options.get("tags"),
                    source_hash=options.get("source_hash"),
                    target_library_id=options.get("target_library_id"),
                    staged_origin=staged_origin,
                ),
                actor_user_id=owner_user_id,
                session_factory=session_factory,
            )

    result = registry.get(job_id)
    completed = result is not None and str(result.state) == "completed"
    with session_factory.scoped_session() as session:
        upload = session.get(ArtifactUploadSession, upload_id)
        if upload is None or upload.state != ArtifactUploadState.INGESTING:
            return
        target = (
            ArtifactUploadState.COMPLETED if completed else ArtifactUploadState.FAILED
        )
        root = staged_path.parents[1] if staged_path is not None else None
        manager = SqlArtifactUploadManager(session, staging_root=root)
        manager.transition(
            upload,
            target,
            error_code=None if completed else "artifact_ingestion_failed",
            retryable=bool(result.retryable) if result is not None else True,
        )
        record_artifact_upload_event(
            "completed" if completed else "failed", upload.adapter_id
        )
        if completed:
            lease = session.exec(
                select(StagingLease).where(StagingLease.job_id == job_id)
            ).first()
            if lease is not None:
                session.delete(lease)
                session.commit()
            manager.adapter_for(upload).abort_owned(upload)


def _upload_id(subject: str) -> str:
    return subject.split("/", 1)[1]


def _step(ctx) -> None:
    run_verified_upload_ingestion(
        upload_id=_upload_id(ctx.subject_key),
        job_id=ctx.job_id,
        session_factory=get_session_factory(),
    )


def _cancel(session: Session, subject: str) -> None:
    upload = session.get(ArtifactUploadSession, _upload_id(subject))
    if upload is None or upload.state != ArtifactUploadState.INGESTING:
        return
    upload.state = ArtifactUploadState.FAILED
    upload.error_code = "artifact_upload_cancelled"
    upload.retryable = False
    upload.version += 1
    session.add(upload)


def _on_failure(session: Session, subject: str, reason: str) -> None:
    upload = session.get(ArtifactUploadSession, _upload_id(subject))
    if upload is None or upload.state != ArtifactUploadState.INGESTING:
        return
    upload.state = ArtifactUploadState.FAILED
    upload.error_code = "artifact_ingestion_failed"
    upload.retryable = True
    upload.version += 1
    session.add(upload)
    del reason


def _recover_uploads() -> dict[str, int]:
    from .recovery import reconcile_artifact_uploads

    outcome = reconcile_artifact_uploads()
    return {
        "reconciled": outcome.reconciled,
        "expired": outcome.expired,
        "retained": outcome.retained,
    }


def definitions():
    from app.db.models import LaneName
    from app.modules.work.contracts import JobDefinition, Step
    from app.modules.work.sources import fixed, scheduled

    return [
        scheduled(
            JobKind.INGESTION_UPLOAD_RECOVERY,
            cron=fixed("55 * * * *"),
            run=_recover_uploads,
            label="Resumable upload expiry",
        ),
        JobDefinition(
            name=JobKind.INGESTION_ARTIFACT_UPLOAD,
            lane=LaneName.INGEST,
            steps=(Step(f"{JobKind.INGESTION_ARTIFACT_UPLOAD.value}.run", _step),),
            cancel=_cancel,
            on_failure=_on_failure,
            retry=lambda _session, _subject: False,
            label="Resumable uploads",
        ),
    ]
