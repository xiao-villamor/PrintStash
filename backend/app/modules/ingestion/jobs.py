"""Job definitions for accepted ingest requests.

Every ``ingest.*`` Job is request-originated: the route records the
``IngestRequest``, its staging leases and the queued Job in one transaction and
nudges. There is no source to scan; the Job row is its own pending marker, and
the reconciler's repair pass is what guarantees it runs (and resumes it after a
crash, a restore or an upgrade).
"""

from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.db.models import (
    FileRevisionStatus,
    FileType,
    IngestRequest,
    IngestRequestKind,
    LaneName,
)
from app.db.session import get_session_factory
from app.modules.work.async_steps import run_async
from app.modules.work.contracts import JobContext, JobDefinition, Step
from app.schemas.ingest import UrlIngestRequest

from . import background, requests, staging_leases
from .import_resolvers import CollectionMember, ModelFile
from .ingestion import StagedArtifact, ingest_staged_file, release_job_staging


def _request(ctx: JobContext) -> IngestRequest:
    with get_session_factory().scoped_session() as session:
        request = requests.load(session, ctx.job_id)
        session.expunge(request)
        return request


def _staged_path(job_id: str) -> Path:
    with get_session_factory().scoped_session() as session:
        leases = staging_leases.job_leases(session, job_id)
    if not leases:
        raise RuntimeError("staging_expired")
    path = Path(leases[0].path)
    if not path.exists():
        raise RuntimeError("staging_expired")
    return path


def _typed(cls: Any, values: list[dict[str, Any]]) -> list[Any]:
    names = {field.name for field in dataclass_fields(cls)}
    return [cls(**{k: v for k, v in value.items() if k in names}) for value in values]


def _upload(ctx: JobContext) -> None:
    request = _request(ctx)
    staged = _staged_path(ctx.job_id)
    selection = requests.selection(request)
    ingest_staged_file(
        job_id=ctx.job_id,
        artifact=StagedArtifact(
            staged_path=staged,
            original_filename=request.original_filename or staged.name,
            model_name=request.model_name or Path(request.original_filename or "").stem,
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
                job_id=ctx.job_id,
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
    request = _request(ctx)
    background.inspect_uploaded_archive(
        job_id=ctx.job_id,
        staged=_staged_path(ctx.job_id),
        original_filename=request.original_filename or "archive.zip",
        cancelled=ctx.cancelled,
    )


def _archive_selection(ctx: JobContext) -> None:
    request = _request(ctx)
    selection = requests.selection(request)
    archive = _staged_path(ctx.job_id)
    background.run_archive_selection(
        job_id=ctx.job_id,
        archive=archive,
        archive_name=str(selection.get("archive_name") or archive.name),
        names=[str(name) for name in selection.get("names", [])],
        collection=request.collection,
        tags=request.tags,
        source_url=request.source_url,
        actor_user_id=request.owner_user_id,
        session_factory=get_session_factory(),
    )
    release_job_staging(ctx.job_id)


def _url_selection(ctx: JobContext) -> None:
    request = _request(ctx)
    selection = requests.selection(request)
    run_async(
        background.run_file_selection_import(
            job_id=ctx.job_id,
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
            job_id=ctx.job_id,
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
    """Withdraw an ingest request: its staged bytes are released now."""
    job_id = _job_id(subject_key)
    for lease in staging_leases.job_leases(session, job_id):
        if lease.capture_upload_slot_origin_id is None:
            Path(lease.path).unlink(missing_ok=True)
            session.delete(lease)
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
        if not leases or not all(Path(lease.path).exists() for lease in leases):
            return False
        staging_leases.renew_job_lease(session, job_id=job_id)
    manifest = json.loads(request.manifest_json or "{}")
    if manifest.get("claimed"):
        return False
    return True


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
        label=label,
    )


def definitions() -> list[JobDefinition]:
    return [
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
