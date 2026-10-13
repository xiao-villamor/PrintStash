"""Job definitions for accepted ingest requests.

Every ``ingest.*`` Job is request-originated: the route records the
``IngestRequest``, its staging leases and the queued Job in one transaction and
nudges. There is no source to scan; the Job row is its own pending marker, and
the reconciler's repair pass is what guarantees it runs (and resumes it after a
crash, a restore or an upgrade).
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from dataclasses import fields as dataclass_fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import (
    FileRevisionStatus,
    FileType,
    IngestionScratchWindow,
    IngestRequest,
    IngestRequestKind,
    JobKind,
    LaneName,
    WorkPriority,
)
from app.db.session import get_session_factory
from app.modules.work.async_steps import run_async
from app.modules.work.contracts import JobContext, JobDefinition, Step, WorkItem
from app.schemas.ingest import UrlIngestRequest

from . import background, requests, staging_leases
from .import_resolvers import CollectionMember, ModelFile
from .ingestion import StagedArtifact, ingest_staged_file, release_job_staging


def _request(ctx: JobContext) -> IngestRequest:
    with get_session_factory().scoped_session() as session:
        request = requests.load(session, ctx.job_id)
        session.expunge(request)
        return request


@contextmanager
def _job_input(ctx: JobContext) -> Iterator[Path]:
    from app.core.cancellation import OperationCancelled, checkpoint
    from app.modules.work.jobs import jobs

    def ensure_authority() -> None:
        if ctx.cancelled():
            raise OperationCancelled()
        checkpoint(force=True)

    try:
        with get_session_factory().scoped_session() as session:
            custody = staging_leases.open_job_input(
                session,
                ctx.job_id,
                checkpoint=ensure_authority,
            )
    except staging_leases.StagingLeaseError as exc:
        raise RuntimeError("staging_expired") from exc
    try:
        with custody as path:
            checkpoint(force=True)
            yield path
    except staging_leases.StagingLeaseError as exc:
        raise RuntimeError("staging_expired") from exc
    finally:
        primary = sys.exception()
        try:
            jobs.reconcile_settled_attempt(
                ctx.job_id,
                ctx.attempt,
                _settled,
                execution_epoch=ctx.execution_epoch,
            )
        except Exception as exc:
            if primary is None:
                raise
            primary.add_note(f"staging settlement cleanup failed: {exc}")


def _typed(cls: Any, values: list[dict[str, Any]]) -> list[Any]:
    names = {field.name for field in dataclass_fields(cls)}
    return [cls(**{k: v for k, v in value.items() if k in names}) for value in values]


def _upload(ctx: JobContext) -> None:
    with _job_input(ctx) as staged:
        request = _request(ctx)
        selection = requests.selection(request)
        ingest_staged_file(
            job_context=ctx,
            artifact=StagedArtifact(
                staged_path=staged,
                original_filename=request.original_filename or staged.name,
                model_name=request.model_name
                or Path(request.original_filename or "").stem,
                file_type=FileType(request.file_type),
                collection=request.collection,
                tags=request.tags,
                source_hash=request.source_hash,
                source_url=request.source_url,
                target_library_id=request.target_library_id,
                native_context=selection.get("native_context"),
                revision_label=selection.get("revision_label"),
                revision_status=(
                    FileRevisionStatus(selection["revision_status"])
                    if selection.get("revision_status")
                    else None
                ),
                revision_notes=selection.get("revision_notes"),
                is_recommended=bool(selection.get("is_recommended")),
                auto_recommend_first_gcode=False,
            ),
            actor_user_id=request.owner_user_id,
            ingestion_key=selection.get("ingestion_key"),
        )


def _url(ctx: JobContext) -> None:
    request = _request(ctx)
    selection = requests.selection(request)
    try:
        run_async(
            background.import_from_url(
                job_context=ctx,
                req=UrlIngestRequest(
                    url=request.source_url or "",
                    collection=request.collection,
                    tags=request.tags,
                    thingiverse_cookie=requests.credential(request),
                    review=bool(selection.get("review")),
                ),
                actor_user_id=request.owner_user_id,
                session_factory=get_session_factory(),
            )
        )
    finally:
        requests.clear_credential(ctx.job_id)


def _archive_inspect(ctx: JobContext) -> None:
    with _job_input(ctx) as staged:
        request = _request(ctx)
        background.inspect_uploaded_archive(
            job_context=ctx,
            staged=staged,
            original_filename=request.original_filename or "archive.zip",
            cancelled=ctx.cancelled,
        )


def _archive_selection(ctx: JobContext) -> None:
    with _job_input(ctx) as staged:
        request = _request(ctx)
        selection = requests.selection(request)
        archive = staged
        background.run_archive_selection(
            job_context=ctx,
            archive=archive,
            archive_name=str(selection.get("archive_name") or archive.name),
            names=[str(name) for name in selection.get("names", [])],
            collection=request.collection,
            tags=request.tags,
            source_url=request.source_url,
            actor_user_id=request.owner_user_id,
            session_factory=get_session_factory(),
        )
        release_job_staging(ctx)


def _url_selection(ctx: JobContext) -> None:
    request = _request(ctx)
    selection = requests.selection(request)
    run_async(
        background.run_file_selection_import(
            job_context=ctx,
            page_url=request.source_url or "",
            files=_typed(ModelFile, list(selection.get("files", []))),
            collection=request.collection,
            tags=request.tags,
            actor_user_id=request.owner_user_id,
            session_factory=get_session_factory(),
        )
    )


def _collection(ctx: JobContext) -> None:
    request = _request(ctx)
    selection = requests.selection(request)
    run_async(
        background.run_collection_member_import(
            job_context=ctx,
            members=_typed(CollectionMember, list(selection.get("members", []))),
            target_collection=str(selection.get("target_collection") or ""),
            tags=request.tags,
            actor_user_id=request.owner_user_id,
            session_factory=get_session_factory(),
        )
    )


def _job_id(subject_key: str) -> str:
    return subject_key.split("/", 1)[1]


def _cancel(session: Session, subject_key: str) -> None:
    """Withdraw intent; physical custody defers cleanup until the actor stops."""
    job_id = _job_id(subject_key)
    from .staging_cleanup import release_job

    release_job(session, job_id)
    _release_scratch(session, job_id)
    request = session.get(IngestRequest, job_id)
    if request is not None:
        request.source_credential = None
        session.add(request)


def _retry(session: Session, subject_key: str) -> bool:
    """A retry is possible while the request (and any staged bytes) remain."""
    job_id = _job_id(subject_key)
    request = session.get(IngestRequest, job_id)
    if request is None:
        return False
    staged_kinds = {
        IngestRequestKind.UPLOAD,
        IngestRequestKind.ARCHIVE_INSPECT,
        IngestRequestKind.ARCHIVE_SELECTION,
    }
    if IngestRequestKind(request.kind) in staged_kinds:
        leases = staging_leases.job_leases(session, job_id)
        if not leases or not all(
            staging_leases._matching_path(lease) is not None
            for lease in leases
            if lease.capture_upload_slot_origin_id is None
        ):
            return False
        staging_leases.renew_job_lease(session, job_id=job_id)
    manifest = json.loads(request.manifest_json or "{}")
    if manifest.get("claimed"):
        return False
    return True


def _release_scratch(session: Session, job_id: str) -> None:
    from sqlalchemy import update

    session.exec(
        update(IngestionScratchWindow)
        .where(col(IngestionScratchWindow.origin_job_id) == job_id)
        .values(available_at=utcnow())
    )


def _settled(session: Session, subject_key: str) -> None:
    from .staging_cleanup import reconcile_jobs

    job_id = _job_id(subject_key)
    _release_scratch(session, job_id)
    reconcile_jobs(session, job_id=job_id)


def _definition(
    kind: IngestRequestKind, lane: LaneName, step: Any, label: str
) -> JobDefinition:
    name = requests.DEFINITIONS[kind]
    return JobDefinition(
        name=name,
        lane=lane,
        steps=(Step(f"{name.value}.run", step),),
        cancel=_cancel,
        retry=_retry,
        on_settled=_settled,
        label=label,
    )


class ScratchCleanupSource:
    """The indexed durable receipt queue; filesystem proof is checked by the job."""

    def pending(self, session: Session, *, now: datetime, limit: int) -> list[WorkItem]:
        identifiers = session.exec(
            select(IngestionScratchWindow.id)
            .where(IngestionScratchWindow.available_at <= now)
            .order_by(
                col(IngestionScratchWindow.available_at), col(IngestionScratchWindow.id)
            )
            .limit(limit)
        ).all()
        return [
            WorkItem(
                subject_key=f"scratch_window/{identifier}",
                priority=WorkPriority.BACKFILL,
            )
            for identifier in identifiers
        ]

    def next_due(self, session: Session, *, now: datetime) -> datetime | None:
        return session.exec(
            select(IngestionScratchWindow.available_at)
            .order_by(
                col(IngestionScratchWindow.available_at), col(IngestionScratchWindow.id)
            )
            .limit(1)
        ).first()


def _cleanup_scratch(ctx: JobContext) -> None:
    from .scratch_windows import cleanup_window

    prefix, separator, identifier = ctx.subject_key.partition("/")
    if prefix != "scratch_window" or not separator or not identifier:
        raise ValueError("invalid_scratch_cleanup_subject")
    if cleanup_window(identifier):
        return
    with get_session_factory().scoped_session() as session:
        row = session.get(IngestionScratchWindow, identifier)
        if row is not None:
            row.available_at = utcnow() + timedelta(seconds=60)
            session.add(row)
            session.commit()


def definitions() -> list[JobDefinition]:
    return [
        JobDefinition(
            name=JobKind.INGESTION_SCRATCH_CLEANUP,
            lane=LaneName.MAINTENANCE,
            steps=(Step("ingestion.scratch_cleanup", _cleanup_scratch),),
            source=ScratchCleanupSource(),
            label="Ingestion temporary files",
        ),
        _definition(IngestRequestKind.UPLOAD, LaneName.INGEST, _upload, "Uploads"),
        _definition(IngestRequestKind.URL, LaneName.NETWORK, _url, "URL imports"),
        _definition(
            IngestRequestKind.ARCHIVE_INSPECT,
            LaneName.INGEST,
            _archive_inspect,
            "Archive inspection",
        ),
        _definition(
            IngestRequestKind.ARCHIVE_SELECTION,
            LaneName.INGEST,
            _archive_selection,
            "Archive imports",
        ),
        _definition(
            IngestRequestKind.URL_SELECTION,
            LaneName.NETWORK,
            _url_selection,
            "Model page imports",
        ),
        _definition(
            IngestRequestKind.COLLECTION,
            LaneName.NETWORK,
            _collection,
            "Collection imports",
        ),
    ]
