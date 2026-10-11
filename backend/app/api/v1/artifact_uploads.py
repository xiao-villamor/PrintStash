"""Canonical provider-neutral routes for durable Artifact uploads."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Literal, cast

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlmodel import Session, select

from app.api.command_actor import CommandActor, command_session, require_command_user
from app.api.command_execution import bounded_command, run_command
from app.core.config import settings
from app.core.ratelimit import rate_limit
from app.core.time import utcnow
from app.db.models import (
    ArtifactUploadPart,
    ArtifactUploadSession,
    ArtifactUploadState,
    CollectionRole,
    JobKind,
    Model,
    User,
)
from app.db.scopes import live
from app.modules.administration import audit
from app.modules.identity import rbac
from app.modules.ingestion import background as ingest_background
from app.modules.ingestion import staging_leases
from app.modules.ingestion.artifact_uploads import (
    ArtifactUploadError,
    ChunkReceipt,
    NativeMultipartError,
    SqlArtifactUploadManager,
    UploadRequest,
)
from app.modules.ingestion.artifact_uploads import handoff as upload_handoff
from app.modules.ingestion.artifact_uploads.api_chunks import CHUNK_SIZE, ApiChunkError
from app.modules.work import nudge
from app.modules.work import service as work_service
from app.schemas.artifact_uploads import (
    ArtifactUploadChunkRead,
    ArtifactUploadCreate,
    ArtifactUploadNativePartInstruction,
    ArtifactUploadNativePartReceipt,
    ArtifactUploadNativePartSign,
    ArtifactUploadPartRead,
    ArtifactUploadPlanRead,
    ArtifactUploadRead,
    UploadPurpose,
)

from .ingest import (
    _require_ingest_collection,
    _validate_target_library,
)

router = APIRouter(prefix="/artifact-uploads", tags=["artifact-uploads"])

_create_limit = rate_limit(30, 60.0)
_plan_limit = rate_limit(180, 60.0)
_chunk_limit = rate_limit(600, 60.0)
_finalize_limit = rate_limit(60, 60.0)
_native_part_limit = rate_limit(600, 60.0)


def _manager(session: Session) -> SqlArtifactUploadManager:
    return SqlArtifactUploadManager(session)


def _part_read(part: ArtifactUploadPart) -> ArtifactUploadPartRead:
    return ArtifactUploadPartRead(
        index=part.part_number - 1,
        offset=part.byte_offset,
        size_bytes=part.size_bytes,
        sha256=part.sha256,
    )


def _upload_read(
    manager: SqlArtifactUploadManager, upload: ArtifactUploadSession
) -> ArtifactUploadRead:
    return ArtifactUploadRead(
        id=upload.id,
        purpose=upload.purpose,
        target_role=upload.target_role,
        target_id=upload.target_id,
        filename=upload.filename,
        media_type=upload.media_type,
        size_bytes=upload.declared_size,
        state=str(getattr(upload.state, "value", upload.state)),
        mode=upload.adapter_id,
        received_bytes=upload.received_bytes,
        verified_size=upload.verified_size,
        verified_sha256=upload.verified_sha256,
        job_id=upload.job_id,
        retryable=upload.retryable,
        error_code=upload.error_code,
        created_at=upload.created_at,
        updated_at=upload.updated_at,
        expires_at=upload.expires_at,
        parts=[_part_read(part) for part in manager.parts(upload)],
    )


def _translate_error(exc: Exception) -> HTTPException:
    code = str(exc)
    if code == "artifact_upload_not_found":
        return HTTPException(status_code=404, detail=code)
    if code in {
        "artifact_upload_state_conflict",
        "artifact_upload_chunk_conflict",
        "artifact_upload_assembly_conflict",
        "artifact_upload_mode_conflict",
        "native_upload_receipts_mismatch",
    }:
        return HTTPException(status_code=409, detail=code)
    if code == "artifact_upload_expired":
        return HTTPException(status_code=410, detail=code)
    if code in {"artifact_upload_size_invalid", "staging_capacity_exceeded"}:
        return HTTPException(status_code=507, detail=code)
    return HTTPException(status_code=422, detail=code)


def _completed_idempotent_race(
    *,
    exc: ArtifactUploadError,
    manager: SqlArtifactUploadManager,
    session: Session,
    session_id: str,
    current_user: User,
    accepted_states: set[ArtifactUploadState],
) -> ArtifactUploadSession | None:
    """Return the winner's state when a duplicate transition loses its CAS."""

    if str(exc) != "artifact_upload_state_conflict":
        return None
    session.expire_all()
    current = manager.get(session_id, current_user)
    if current.state in accepted_states:
        return current
    return None


def _validate_purpose_file(request: ArtifactUploadCreate) -> None:
    suffix = Path(request.filename).suffix.lower()
    if request.purpose in {"gcode", "slicer", "revision"}:
        allowed = suffix in ingest_background.GCODE_SUFFIXES
    elif request.purpose in {"model", "external_writeback"}:
        allowed = suffix in ingest_background.MESH_SUFFIXES
    elif request.purpose == "archive":
        allowed = suffix == ".zip"
    else:
        allowed = True
    if not allowed:
        raise HTTPException(status_code=400, detail="unsupported_file_type")


def _require_revision_target(
    session: Session, user: User, request: ArtifactUploadCreate
) -> None:
    if request.purpose != "revision":
        return
    if request.target_role != "model_revision" or request.target_id is None:
        raise HTTPException(status_code=422, detail="revision_target_required")
    try:
        model_id = int(request.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="revision_target_invalid") from exc
    model = session.exec(select(Model).where(Model.id == model_id, live(Model))).first()
    if model is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    rbac.require_model_collection_role(
        session, user, model.collection_id, CollectionRole.EDIT
    )


@router.post(
    "",
    response_model=ArtifactUploadRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_create_limit)],
)
@bounded_command
def create_artifact_upload(
    request: ArtifactUploadCreate,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
    actor: CommandActor = Depends(require_command_user),
) -> ArtifactUploadRead:
    with command_session(actor) as (session, current_user):
        if request.size_bytes > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="upload_too_large")
        _validate_purpose_file(request)
        _require_revision_target(session, current_user, request)
        if request.purpose != "revision":
            _require_ingest_collection(session, current_user, request.collection)
            _validate_target_library(session, request.target_library_id)
        manager = _manager(session)
        try:
            upload = manager.create(
                UploadRequest(
                    purpose=request.purpose,
                    target_role=request.target_role,
                    target_id=request.target_id,
                    filename=request.filename,
                    media_type=request.media_type,
                    size_bytes=request.size_bytes,
                    client_sha256=request.sha256,
                    options={
                        "model_name": request.model_name,
                        "collection": request.collection,
                        "tags": request.tags,
                        "source_hash": request.source_hash,
                        "target_library_id": request.target_library_id,
                        "revision_label": request.revision_label,
                        "revision_status": request.revision_status.value
                        if request.revision_status
                        else None,
                        "revision_notes": request.revision_notes,
                        "is_recommended": request.is_recommended,
                        "_idempotency_key": idempotency_key,
                    },
                ),
                current_user,
            )
        except (ArtifactUploadError, ApiChunkError, NativeMultipartError) as exc:
            raise _translate_error(exc) from exc
        audit.record(
            session,
            action="artifact_upload.create",
            resource_type="artifact_upload",
            diff={
                "session_id": upload.id,
                "purpose": upload.purpose,
                "mode": upload.adapter_id,
                "bytes": upload.declared_size,
            },
        )
        return _upload_read(manager, upload)


@router.get("/{session_id}", response_model=ArtifactUploadRead)
@bounded_command
def get_artifact_upload(
    session_id: str,
    response: Response,
    actor: CommandActor = Depends(require_command_user),
) -> ArtifactUploadRead:
    with command_session(actor) as (session, current_user):
        response.headers["Cache-Control"] = "no-store"
        manager = _manager(session)
        try:
            return _upload_read(manager, manager.get(session_id, current_user))
        except (ArtifactUploadError, NativeMultipartError) as exc:
            raise _translate_error(exc) from exc


@router.get(
    "/{session_id}/plan",
    response_model=ArtifactUploadPlanRead,
    dependencies=[Depends(_plan_limit)],
)
@bounded_command
def get_artifact_upload_plan(
    session_id: str,
    response: Response,
    actor: CommandActor = Depends(require_command_user),
) -> ArtifactUploadPlanRead:
    with command_session(actor) as (session, current_user):
        response.headers["Cache-Control"] = "no-store"
        manager = _manager(session)
        try:
            upload = manager.get(session_id, current_user)
            plan = manager.plan(session_id, current_user)
        except ArtifactUploadError as exc:
            raise _translate_error(exc) from exc
        return ArtifactUploadPlanRead(
            session_id=upload.id,
            mode=cast(Literal["api_chunks", "native_parts", "simple"], plan.mode),
            chunk_size=plan.chunk_size,
            max_parallel=plan.max_parallel,
            upload_path=plan.upload_path,
            uploaded_parts=[_part_read(part) for part in manager.parts(upload)],
            expires_at=upload.expires_at,
        )


@router.post(
    "/{session_id}/parts/{part_number}/sign",
    response_model=ArtifactUploadNativePartInstruction,
    dependencies=[Depends(_native_part_limit)],
)
@bounded_command
def sign_artifact_upload_part(
    session_id: str,
    part_number: int,
    body: ArtifactUploadNativePartSign,
    response: Response,
    actor: CommandActor = Depends(require_command_user),
) -> ArtifactUploadNativePartInstruction:
    with command_session(actor) as (session, current_user):
        response.headers["Cache-Control"] = "no-store"
        manager = _manager(session)
        try:
            url = manager.sign_native_part(
                session_id,
                part_number=part_number,
                checksum_sha256=body.checksum_sha256,
                actor=current_user,
            )
        except (ArtifactUploadError, NativeMultipartError, ValueError) as exc:
            raise _translate_error(exc) from exc
        checksum = base64.b64encode(bytes.fromhex(body.checksum_sha256)).decode()
        return ArtifactUploadNativePartInstruction(
            url=url,
            headers={"x-amz-checksum-sha256": checksum},
            expires_at=utcnow() + timedelta(seconds=60),
        )


@router.post(
    "/{session_id}/parts/{part_number}",
    response_model=ArtifactUploadChunkRead,
    dependencies=[Depends(_native_part_limit)],
)
@bounded_command
def record_artifact_upload_part(
    session_id: str,
    part_number: int,
    body: ArtifactUploadNativePartReceipt,
    actor: CommandActor = Depends(require_command_user),
) -> ArtifactUploadChunkRead:
    with command_session(actor) as (session, current_user):
        manager = _manager(session)
        try:
            part = manager.record_native_part(
                session_id,
                part_number=part_number,
                size_bytes=body.size_bytes,
                checksum_sha256=body.checksum_sha256,
                etag=body.etag,
                actor=current_user,
            )
            upload = manager.get(session_id, current_user)
        except (ArtifactUploadError, NativeMultipartError, ValueError) as exc:
            raise _translate_error(exc) from exc
        return ArtifactUploadChunkRead(
            session=_upload_read(manager, upload),
            part=_part_read(part),
        )


@router.put(
    "/{session_id}/chunks/{index}",
    response_model=ArtifactUploadChunkRead,
    dependencies=[Depends(_chunk_limit)],
)
async def put_artifact_upload_chunk(
    session_id: str,
    index: int,
    request: Request,
    offset: int = Query(ge=0),
    length: int = Query(gt=0, le=CHUNK_SIZE),
    sha256: str = Query(pattern=r"^[0-9a-fA-F]{64}$"),
    actor: CommandActor = Depends(require_command_user),
) -> ArtifactUploadChunkRead:
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > CHUNK_SIZE:
                raise HTTPException(status_code=413, detail="upload_chunk_too_large")
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="content_length_invalid"
            ) from exc
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > CHUNK_SIZE:
            raise HTTPException(status_code=413, detail="upload_chunk_too_large")
        payload.extend(chunk)

    return await run_command(
        _record_received_chunk,
        actor,
        session_id,
        index,
        offset,
        length,
        sha256,
        payload,
    )


def _record_received_chunk(
    actor: CommandActor,
    session_id: str,
    index: int,
    offset: int,
    length: int,
    sha256: str,
    payload: bytearray,
) -> ArtifactUploadChunkRead:
    with command_session(actor) as (session, current_user):
        manager = _manager(session)
        try:
            part = manager.record_chunk(
                session_id,
                ChunkReceipt(
                    index=index,
                    offset=offset,
                    size_bytes=length,
                    sha256=sha256.lower(),
                ),
                bytes(payload),
                current_user,
            )
            upload = manager.get(session_id, current_user)
        except (ArtifactUploadError, ApiChunkError, NativeMultipartError) as exc:
            raise _translate_error(exc) from exc
        return ArtifactUploadChunkRead(
            session=_upload_read(manager, upload),
            part=_part_read(part),
        )


@router.post(
    "/{session_id}/finalize",
    response_model=ArtifactUploadRead,
    dependencies=[Depends(_finalize_limit)],
)
@bounded_command
def finalize_artifact_upload(
    session_id: str,
    actor: CommandActor = Depends(require_command_user),
) -> ArtifactUploadRead:
    with command_session(actor) as (session, current_user):
        manager = _manager(session)
        # Authorize before creating or locking any private staging directory.
        try:
            pending = manager.get(session_id, current_user)
        except ArtifactUploadError as exc:
            raise _translate_error(exc) from exc
        if (
            pending.state
            in {ArtifactUploadState.INGESTING, ArtifactUploadState.COMPLETED}
            and pending.job_id is not None
        ):
            return _upload_read(manager, pending)
        with manager.operation(session_id) as acquired:
            if not acquired:
                raise HTTPException(
                    status_code=409, detail="artifact_upload_state_conflict"
                )
            try:
                session.refresh(pending)
                if pending.state in {
                    ArtifactUploadState.INGESTING,
                    ArtifactUploadState.COMPLETED,
                }:
                    return _upload_read(manager, pending)
                options = json.loads(pending.request_json)
                pending_request = ArtifactUploadCreate(
                    purpose=cast(UploadPurpose, pending.purpose),
                    target_role=pending.target_role,
                    target_id=pending.target_id,
                    filename=pending.filename,
                    media_type=pending.media_type,
                    size_bytes=pending.declared_size,
                    sha256=pending.client_sha256,
                    **options,
                )
                _require_revision_target(
                    session,
                    current_user,
                    pending_request,
                )
                if pending.purpose != "revision":
                    _require_ingest_collection(
                        session, current_user, options.get("collection")
                    )
                    _validate_target_library(session, options.get("target_library_id"))
                upload, verified = manager.finalize(session_id, current_user)
            except (ArtifactUploadError, ApiChunkError, NativeMultipartError) as exc:
                raise _translate_error(exc) from exc
            assert current_user.id is not None
            # This compare-and-set claim closes the small window between verification
            # and job creation. A concurrent finalize loses here before it can create a
            # second Job or lease for the same immutable staged object.
            try:
                manager.transition(upload, ArtifactUploadState.INGESTING)
            except ArtifactUploadError as exc:
                current = _completed_idempotent_race(
                    exc=exc,
                    manager=manager,
                    session=session,
                    session_id=session_id,
                    current_user=current_user,
                    accepted_states={
                        ArtifactUploadState.INGESTING,
                        ArtifactUploadState.COMPLETED,
                    },
                )
                if current is not None:
                    return _upload_read(manager, current)
                raise _translate_error(exc) from exc
            try:
                job_id = uuid.uuid4().hex
                work_service.request(
                    session,
                    definition=JobKind.INGESTION_ARTIFACT_UPLOAD,
                    subject_key=upload_handoff.subject_key(upload.id),
                    owner_user_id=current_user.id,
                    job_id=job_id,
                )
                staging_leases.create_job_lease(
                    session,
                    job_id=job_id,
                    owner_user_id=current_user.id,
                    path=verified.materialize(),
                    size_bytes=verified.size_bytes,
                    sha256=verified.sha256,
                )
                # One commit records the queued Job, its lease and the session's link.
                manager.transition(upload, ArtifactUploadState.INGESTING, job_id=job_id)
            except Exception as exc:
                session.rollback()
                manager.transition(
                    upload,
                    ArtifactUploadState.FAILED,
                    error_code="artifact_upload_ingestion_claim_failed",
                    retryable=True,
                )
                if isinstance(exc, staging_leases.StagingCapacityExceeded):
                    raise HTTPException(
                        status_code=507, detail="staging_capacity_exceeded"
                    ) from exc
                raise
            audit.record(
                session,
                action="artifact_upload.finalize",
                resource_type="artifact_upload",
                diff={
                    "session_id": upload.id,
                    "purpose": upload.purpose,
                    "mode": upload.adapter_id,
                    "bytes": verified.size_bytes,
                    "job_id": job_id,
                },
            )
            nudge(JobKind.INGESTION_ARTIFACT_UPLOAD)
            return _upload_read(manager, upload)


@router.delete("/{session_id}", response_model=ArtifactUploadRead)
@bounded_command
def abort_artifact_upload(
    session_id: str,
    actor: CommandActor = Depends(require_command_user),
) -> ArtifactUploadRead:
    with command_session(actor) as (session, current_user):
        manager = _manager(session)
        try:
            upload = manager.abort(session_id, current_user)
        except (ArtifactUploadError, ApiChunkError, NativeMultipartError) as exc:
            if isinstance(exc, ArtifactUploadError):
                current = _completed_idempotent_race(
                    exc=exc,
                    manager=manager,
                    session=session,
                    session_id=session_id,
                    current_user=current_user,
                    accepted_states={ArtifactUploadState.ABORTED},
                )
                if current is not None:
                    return _upload_read(manager, current)
            raise _translate_error(exc) from exc
        audit.record(
            session,
            action="artifact_upload.abort",
            resource_type="artifact_upload",
            diff={
                "session_id": upload.id,
                "purpose": upload.purpose,
                "mode": upload.adapter_id,
                "bytes": upload.received_bytes,
            },
        )
        return _upload_read(manager, upload)
