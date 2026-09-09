"""Canonical provider-neutral routes for durable Artifact uploads."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlmodel import Session, select

from app.core.config import settings
from app.core.ratelimit import rate_limit
from app.core.security import require_user
from app.db.models import (
    ArtifactUploadPart,
    ArtifactUploadSession,
    ArtifactUploadState,
    CollectionRole,
    Model,
    User,
)
from app.db.scopes import live
from app.db.session import SessionFactory, get_session, get_session_factory
from app.modules.identity import rbac
from app.modules.ingestion import background as ingest_background
from app.modules.ingestion.artifact_uploads import (
    ArtifactUploadError,
    ChunkReceipt,
    SqlArtifactUploadManager,
    UploadRequest,
)
from app.modules.ingestion.artifact_uploads.api_chunks import CHUNK_SIZE, ApiChunkError
from app.modules.ingestion.artifact_uploads.handoff import run_verified_upload_ingestion
from app.schemas.artifact_uploads import (
    ArtifactUploadChunkRead,
    ArtifactUploadCreate,
    ArtifactUploadPartRead,
    ArtifactUploadPlanRead,
    ArtifactUploadRead,
)

from .ingest import (
    _create_staged_job,
    _require_ingest_collection,
    _validate_target_library,
)

router = APIRouter(prefix="/artifact-uploads", tags=["artifact-uploads"])

_create_limit = rate_limit(30, 60.0)
_plan_limit = rate_limit(180, 60.0)
_chunk_limit = rate_limit(600, 60.0)
_finalize_limit = rate_limit(60, 60.0)


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
        mode="api_chunks",
        received_bytes=upload.received_bytes,
        verified_size=upload.verified_size,
        verified_sha256=upload.verified_sha256,
        job_id=upload.background_job_id,
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
    }:
        return HTTPException(status_code=409, detail=code)
    if code == "artifact_upload_expired":
        return HTTPException(status_code=410, detail=code)
    if code in {"artifact_upload_size_invalid", "staging_capacity_exceeded"}:
        return HTTPException(status_code=507, detail=code)
    return HTTPException(status_code=422, detail=code)


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
async def create_artifact_upload(
    request: ArtifactUploadCreate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ArtifactUploadRead:
    if request.size_bytes > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="upload_too_large")
    _validate_purpose_file(request)
    _require_revision_target(session, current_user, request)
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
                },
            ),
            current_user,
        )
    except (ArtifactUploadError, ApiChunkError) as exc:
        raise _translate_error(exc) from exc
    return _upload_read(manager, upload)


@router.get("/{session_id}", response_model=ArtifactUploadRead)
def get_artifact_upload(
    session_id: str,
    response: Response,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ArtifactUploadRead:
    response.headers["Cache-Control"] = "no-store"
    manager = _manager(session)
    try:
        return _upload_read(manager, manager.get(session_id, current_user))
    except ArtifactUploadError as exc:
        raise _translate_error(exc) from exc


@router.get(
    "/{session_id}/plan",
    response_model=ArtifactUploadPlanRead,
    dependencies=[Depends(_plan_limit)],
)
def get_artifact_upload_plan(
    session_id: str,
    response: Response,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ArtifactUploadPlanRead:
    response.headers["Cache-Control"] = "no-store"
    manager = _manager(session)
    try:
        upload = manager.get(session_id, current_user)
        plan = manager.plan(session_id, current_user)
    except ArtifactUploadError as exc:
        raise _translate_error(exc) from exc
    return ArtifactUploadPlanRead(
        session_id=upload.id,
        mode=plan.mode,
        chunk_size=plan.chunk_size,
        max_parallel=plan.max_parallel,
        upload_path=plan.upload_path,
        uploaded_parts=[_part_read(part) for part in manager.parts(upload)],
        expires_at=upload.expires_at,
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
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
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
        payload.extend(chunk)
        if len(payload) > CHUNK_SIZE:
            raise HTTPException(status_code=413, detail="upload_chunk_too_large")

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
    except (ArtifactUploadError, ApiChunkError) as exc:
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
def finalize_artifact_upload(
    session_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> ArtifactUploadRead:
    manager = _manager(session)
    try:
        pending = manager.get(session_id, current_user)
        options = json.loads(pending.request_json)
        _require_revision_target(
            session,
            current_user,
            ArtifactUploadCreate(
                purpose=pending.purpose,
                target_role=pending.target_role,
                target_id=pending.target_id,
                filename=pending.filename,
                media_type=pending.media_type,
                size_bytes=pending.declared_size,
                sha256=pending.client_sha256,
                **options,
            ),
        )
        _require_ingest_collection(session, current_user, options.get("collection"))
        _validate_target_library(session, options.get("target_library_id"))
        upload, verified = manager.finalize(session_id, current_user)
    except (ArtifactUploadError, ApiChunkError) as exc:
        raise _translate_error(exc) from exc
    assert current_user.id is not None
    job_id = _create_staged_job(
        session,
        kind=f"artifact_upload_{upload.purpose}",
        staged=verified.materialize(),
        size=verified.size_bytes,
        sha256=verified.sha256,
        owner_user_id=current_user.id,
    )
    manager.transition(
        upload,
        ArtifactUploadState.INGESTING,
        background_job_id=job_id,
    )
    background_tasks.add_task(
        run_verified_upload_ingestion,
        upload_id=upload.id,
        job_id=job_id,
        staged_path=verified.path,
        session_factory=session_factory,
    )
    return _upload_read(manager, upload)


@router.delete("/{session_id}", response_model=ArtifactUploadRead)
def abort_artifact_upload(
    session_id: str,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ArtifactUploadRead:
    manager = _manager(session)
    try:
        upload = manager.abort(session_id, current_user)
    except (ArtifactUploadError, ApiChunkError) as exc:
        raise _translate_error(exc) from exc
    return _upload_read(manager, upload)
