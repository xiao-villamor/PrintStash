"""Ingestion endpoints: OrcaSlicer G-code + mesh uploads."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File as UploadFileParam,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger
from sqlmodel import Session, select

from app.core.security import require_auth, require_user
from app.db.models import Collection, CollectionRole, SUFFIX_TO_FILE_TYPE, User
from app.db.scopes import live
from app.db.session import SessionFactory, get_session, get_session_factory
from app.schemas.ingest import IngestJobStatus, IngestResponse
from app.services import rbac, storage, taxonomy
from app.services.ingestion import ingest_mesh, ingest_orca_gcode
from app.services.jobs import registry

logger = get_logger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

_GCODE_SUFFIXES = {".gcode", ".g", ".gco"}
_MESH_SUFFIXES = {".stl", ".3mf", ".obj"}


def _collection_path_for(raw_path: str) -> str:
    segments = [taxonomy.slugify(s.strip()) for s in raw_path.split("/") if s.strip()]
    return "/".join(segments)


def _require_ingest_collection(
    session: Session,
    user: User,
    collection: str | None,
) -> None:
    if not collection or not collection.strip():
        if not user.is_superuser:
            raise HTTPException(status_code=403, detail="collection_required")
        return
    collection_path = _collection_path_for(collection)
    row = session.exec(
        select(Collection).where(Collection.path == collection_path, live(Collection))
    ).first()
    if row is None:
        if not user.is_superuser:
            raise HTTPException(
                status_code=403,
                detail="collection_permission_denied",
            )
        return
    rbac.require_collection_role(session, user, row.id, CollectionRole.EDIT)


def _stage_upload(upload: UploadFile, suffix: str) -> Path:
    """Stream an UploadFile into the staging dir; reject if it exceeds the limit."""
    staged = settings.incoming_dir / f"{uuid.uuid4().hex}{suffix}"
    written = storage.stream_to_path(upload.file, staged)
    if written > settings.max_upload_bytes:
        staged.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="upload_too_large",
        )
    return staged


def _resolve_name(model_name: Optional[str], original_filename: str) -> str:
    """Return a non-empty display name, falling back to the file stem."""
    stem = Path(original_filename).stem
    return (model_name or stem).strip() or stem


@router.post(
    "/orca",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Ingest a sliced G-code file from OrcaSlicer",
    description=(
        "Multipart upload from the OrcaSlicer post-processing hook. The G-code is "
        "staged immediately and processed asynchronously: hashed, parsed for slicer "
        "metadata, thumbnail-extracted, deduplicated, and persisted. Returns a "
        "job_id you can poll via GET /ingest/jobs/{job_id}."
    ),
)
async def ingest_orca(
    background_tasks: BackgroundTasks,
    file: UploadFile = UploadFileParam(..., description="The .gcode file"),
    model_name: Optional[str] = Form(None, description="Display name for the model"),
    collection: Optional[str] = Form(
        None, description="Optional collection, e.g. 'Functional/Brackets'"
    ),
    tags: Optional[str] = Form(None, description="Comma-separated tag list"),
    source_hash: Optional[str] = Form(
        None, description="Optional sha256 of the source mesh for dedup"
    ),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")

    original_filename = Path(file.filename).name
    suffix = Path(original_filename).suffix.lower() or ".gcode"
    if suffix not in _GCODE_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    _require_ingest_collection(session, current_user, collection)

    staged = await run_in_threadpool(_stage_upload, file, suffix)
    job_id = registry.create(owner_user_id=current_user.id)
    background_tasks.add_task(
        ingest_orca_gcode,
        job_id=job_id,
        staged_path=staged,
        original_filename=original_filename,
        model_name=_resolve_name(model_name, original_filename),
        collection=collection,
        tags=tags,
        source_hash=source_hash,
        actor_user_id=current_user.id,
        session_factory=session_factory,
    )
    return IngestResponse(job_id=job_id, state="pending")


@router.post(
    "/model",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Ingest a source mesh file (STL, 3MF, OBJ)",
    description=(
        "Multipart upload of a source mesh. The file is staged and processed "
        "asynchronously: hashed, geometry extracted via trimesh (bounding box, "
        "volume, triangle count), a PNG thumbnail rendered, deduplicated, and "
        "persisted. Returns a job_id you can poll via GET /ingest/jobs/{job_id}."
    ),
)
async def ingest_model(
    background_tasks: BackgroundTasks,
    file: UploadFile = UploadFileParam(..., description="The .stl, .3mf, or .obj file"),
    model_name: Optional[str] = Form(None, description="Display name for the model"),
    collection: Optional[str] = Form(
        None, description="Optional collection, e.g. 'Functional/Brackets'"
    ),
    tags: Optional[str] = Form(None, description="Comma-separated tag list"),
    source_hash: Optional[str] = Form(None, description="Optional sha256 for dedup"),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")

    original_filename = Path(file.filename).name
    suffix = Path(original_filename).suffix.lower()
    if suffix not in _MESH_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    _require_ingest_collection(session, current_user, collection)

    staged = await run_in_threadpool(_stage_upload, file, suffix)
    job_id = registry.create(owner_user_id=current_user.id)
    background_tasks.add_task(
        ingest_mesh,
        job_id=job_id,
        staged_path=staged,
        original_filename=original_filename,
        model_name=_resolve_name(model_name, original_filename),
        collection=collection,
        tags=tags,
        file_type=SUFFIX_TO_FILE_TYPE[suffix],
        source_hash=source_hash,
        actor_user_id=current_user.id,
        session_factory=session_factory,
    )
    return IngestResponse(job_id=job_id, state="pending")


@router.get(
    "/jobs/{job_id}",
    response_model=IngestJobStatus,
    summary="Get the status of an ingestion job",
)
def get_job(
    job_id: str,
    current_user: User = Depends(require_user),
) -> IngestJobStatus:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    if (
        job.owner_user_id is not None
        and job.owner_user_id != current_user.id
        and not current_user.is_superuser
    ):
        raise HTTPException(status_code=404, detail="job_not_found")
    return job
