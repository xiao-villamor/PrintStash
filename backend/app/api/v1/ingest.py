"""Ingestion endpoints: OrcaSlicer G-code + mesh uploads."""

from __future__ import annotations

import hashlib
import shutil
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi import (
    File as UploadFileParam,
)
from printstash_core.files import slugify
from sqlalchemy import func
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import require_auth, require_user
from app.core.time import utcnow
from app.db.models import (
    SUFFIX_TO_FILE_TYPE,
    Collection,
    CollectionRole,
    StagingLease,
    User,
)
from app.db.scopes import live
from app.db.session import SessionFactory, get_session, get_session_factory
from app.modules.identity import rbac
from app.modules.ingestion import background as ingest_background
from app.modules.ingestion import importer
from app.modules.ingestion.ingestion import ingest_mesh, ingest_orca_gcode
from app.modules.storage import storage
from app.runtime.jobs import registry
from app.schemas.ingest import (
    ArchiveManifest,
    ArchiveSelectRequest,
    CollectionSelectRequest,
    FileSelectRequest,
    IngestJobStatus,
    IngestResponse,
    UrlIngestRequest,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _collection_path_for(raw_path: str) -> str:
    segments = [slugify(s.strip()) for s in raw_path.split("/") if s.strip()]
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


def _validate_target_library(session: Session, library_id: int | None) -> None:
    """Reject a write-back target unless mirroring is on and the library exists."""
    if library_id is None:
        return
    from app.db.models import ExternalLibrary
    from app.modules.administration.runtime_config import external_libraries_enabled

    if not external_libraries_enabled(session):
        raise HTTPException(status_code=400, detail="external_libraries_disabled")
    library = session.get(ExternalLibrary, library_id)
    if library is None or not library.enabled:
        raise HTTPException(status_code=400, detail="library_not_found")


def _stage_upload(upload: UploadFile, suffix: str) -> tuple[Path, int, str]:
    """Stream an UploadFile into the staging dir; reject if it exceeds the limit."""
    staged = settings.incoming_dir / f"{uuid.uuid4().hex}{suffix}"
    digest = hashlib.sha256()
    from app.modules.storage.capacity import CapacityManager, CapacityResource

    declared_size = getattr(upload, "size", None)
    estimate = declared_size if declared_size is not None else settings.max_upload_bytes
    try:
        with CapacityManager(get_session_factory()).hold(
            f"upload:{staged.name}",
            [CapacityResource.for_path(staged.parent, estimate, role="upload staging")],
        ):
            size = storage.stream_to_path(
                upload.file,
                staged,
                max_bytes=settings.max_upload_bytes,
                digest=digest,
            )
    except storage.UploadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="upload_too_large",
        ) from exc
    return staged, size, digest.hexdigest()


def _record_staging_lease(
    session: Session,
    *,
    job_id: str,
    staged: Path,
    size: int,
    sha256: str,
    owner_user_id: int | None,
) -> None:
    stat = staged.stat(follow_symlinks=False)
    session.add(
        StagingLease(
            id=uuid.uuid4().hex,
            path=str(staged),
            owner_user_id=owner_user_id,
            background_job_id=job_id,
            size_bytes=size,
            sha256=sha256,
            device=stat.st_dev,
            inode=stat.st_ino,
            ctime_ns=stat.st_ctime_ns,
            expires_at=utcnow() + timedelta(hours=24),
        )
    )
    session.commit()


def _create_staged_job(
    session: Session,
    *,
    kind: str,
    staged: Path,
    size: int,
    sha256: str,
    owner_user_id: int,
    check_capacity: bool = True,
    remove_staged_on_failure: bool = True,
) -> str:
    if check_capacity:
        try:
            _require_staging_capacity(session, size=size, owner_user_id=owner_user_id)
        except HTTPException:
            if remove_staged_on_failure:
                staged.unlink(missing_ok=True)
            raise
    job_id = registry.create(owner_user_id=owner_user_id, kind=kind)
    try:
        _record_staging_lease(
            session,
            job_id=job_id,
            staged=staged,
            size=size,
            sha256=sha256,
            owner_user_id=owner_user_id,
        )
    except Exception:
        if remove_staged_on_failure:
            staged.unlink(missing_ok=True)
        registry.finish(
            job_id,
            state="failed",
            error="staging_lease_failed",
            retryable=True,
        )
        raise
    return job_id


def _require_staging_capacity(
    session: Session,
    *,
    size: int,
    owner_user_id: int,
) -> None:
    """Reject work before claiming or deleting an already-verified object."""

    lease_count, staged_bytes = session.exec(
        select(
            func.count(StagingLease.id),
            func.coalesce(func.sum(StagingLease.size_bytes), 0),
        )
    ).one()
    user_leases = session.exec(
        select(func.count(StagingLease.id)).where(
            StagingLease.owner_user_id == owner_user_id
        )
    ).one()
    disk_free = shutil.disk_usage(settings.incoming_dir).free
    capacity_exceeded = (
        int(lease_count) >= settings.staging_max_pending
        or int(user_leases) >= settings.staging_max_active_per_user
        or int(staged_bytes) + size > settings.staging_max_gb * 1024**3
        or disk_free < settings.staging_min_free_gb * 1024**3
    )
    if capacity_exceeded:
        raise HTTPException(status_code=507, detail="staging_capacity_exceeded")


def _resolve_name(model_name: Optional[str], original_filename: str) -> str:
    """Return a non-empty display name, falling back to the file stem."""
    stem = Path(original_filename).stem
    return (model_name or stem).strip() or stem


# --------------------------------------------------------------------------- #
# Pending manifests for the two-step review flows (per-file + collection).
# Unlike the archive registry these hold only metadata (nothing is downloaded
# until the user selects), so there is no staged blob to clean up on prune.
# --------------------------------------------------------------------------- #


def _owns(owner_user_id: Optional[int], user: User) -> bool:
    return owner_user_id is None or owner_user_id == user.id or user.is_superuser


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
    target_library_id: Optional[int] = Form(
        None,
        description="Write the blob into this external (NAS) library instead of vault",
    ),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")

    original_filename = Path(file.filename).name
    suffix = Path(original_filename).suffix.lower() or ".gcode"
    if suffix not in ingest_background.GCODE_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    if source_hash and (
        len(source_hash) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in source_hash)
    ):
        raise HTTPException(status_code=422, detail="source_hash_invalid")
    _require_ingest_collection(session, current_user, collection)
    _validate_target_library(session, target_library_id)

    staged, staged_size, staged_hash = await run_in_threadpool(
        _stage_upload, file, suffix
    )
    assert current_user.id is not None
    job_id = _create_staged_job(
        session,
        kind="gcode",
        staged=staged,
        size=staged_size,
        sha256=staged_hash,
        owner_user_id=current_user.id,
    )
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
        target_library_id=target_library_id,
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
    target_library_id: Optional[int] = Form(
        None,
        description="Write the blob into this external (NAS) library instead of vault",
    ),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")

    original_filename = Path(file.filename).name
    suffix = Path(original_filename).suffix.lower()
    if suffix not in ingest_background.MESH_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    _require_ingest_collection(session, current_user, collection)
    _validate_target_library(session, target_library_id)

    staged, staged_size, staged_hash = await run_in_threadpool(
        _stage_upload, file, suffix
    )
    assert current_user.id is not None
    job_id = _create_staged_job(
        session,
        kind="model",
        staged=staged,
        size=staged_size,
        sha256=staged_hash,
        owner_user_id=current_user.id,
    )
    background_tasks.add_task(
        ingest_mesh,
        job_id=job_id,
        staged_path=staged,
        original_filename=original_filename,
        model_name=_resolve_name(model_name, original_filename),
        collection=collection,
        tags=tags,
        file_type=SUFFIX_TO_FILE_TYPE[suffix],
        source_hash=None,
        actor_user_id=current_user.id,
        session_factory=session_factory,
        target_library_id=target_library_id,
    )
    return IngestResponse(job_id=job_id, state="pending")


@router.post(
    "/url",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Import a model from a direct file or .zip URL",
    description=(
        "Download a user-supplied URL server-side (SSRF-guarded) and ingest it. "
        "A direct mesh/G-code URL is imported immediately; a .zip resolves to an "
        "archive manifest (job result) for selective import via "
        "POST /ingest/archive/{archive_id}/select."
    ),
)
async def ingest_url(
    background_tasks: BackgroundTasks,
    req: UrlIngestRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="url_required")
    try:
        importer.validate_public_url(req.url.strip())
    except importer.ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _require_ingest_collection(session, current_user, req.collection)

    assert current_user.id is not None
    job_id = registry.create(owner_user_id=current_user.id, kind="url")
    background_tasks.add_task(
        ingest_background.import_from_url,
        job_id=job_id,
        req=req,
        actor_user_id=current_user.id,
        session_factory=session_factory,
    )
    return IngestResponse(job_id=job_id, state="pending")


@router.post(
    "/archive",
    response_model=ArchiveManifest,
    dependencies=[Depends(require_auth)],
    summary="Stage a .zip archive and return its importable entries",
    description=(
        "Upload a .zip; it is staged and inspected (zip-slip/zip-bomb guarded) "
        "and the importable 3D entries are returned for selective import via "
        "POST /ingest/archive/{archive_id}/select."
    ),
)
async def ingest_archive(
    file: UploadFile = UploadFileParam(..., description="The .zip archive"),
    current_user: User = Depends(require_user),
) -> ArchiveManifest:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")
    original_filename = Path(file.filename).name
    suffix = Path(original_filename).suffix.lower()
    if suffix != ".zip":
        raise HTTPException(status_code=400, detail="unsupported_file_type")

    staged, _staged_size, _staged_hash = await run_in_threadpool(
        _stage_upload, file, ".zip"
    )
    if not zipfile.is_zipfile(staged):
        staged.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="archive_invalid")
    try:
        entries = await run_in_threadpool(importer.inspect_archive, staged)
    except importer.ImportError_ as exc:
        staged.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pending = importer.PendingArchive(
        path=staged,
        archive_name=original_filename,
        owner_user_id=current_user.id,
        entries=entries,
    )
    archive_id = importer.archives.add(pending)
    return ingest_background.manifest_from_pending(archive_id, pending)


@router.post(
    "/archive/inspect",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Stage and inspect a ZIP archive in the background",
)
async def inspect_archive_background(
    background_tasks: BackgroundTasks,
    file: UploadFile = UploadFileParam(..., description="The .zip archive"),
    current_user: User = Depends(require_user),
) -> IngestResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")
    original_filename = Path(file.filename).name
    if Path(original_filename).suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    staged, _staged_size, _staged_hash = await run_in_threadpool(
        _stage_upload, file, ".zip"
    )
    if not zipfile.is_zipfile(staged):
        staged.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="archive_invalid")
    assert current_user.id is not None
    job_id = registry.create(owner_user_id=current_user.id, kind="archive_manifest")
    background_tasks.add_task(
        ingest_background.inspect_uploaded_archive,
        job_id=job_id,
        staged=staged,
        original_filename=original_filename,
        actor_user_id=current_user.id,
    )
    return IngestResponse(
        job_id=job_id, state="pending", message="archive inspection queued"
    )


@router.post(
    "/archive/{archive_id}/select",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Import selected entries from a staged archive",
    description=(
        "Extract the chosen entries from a previously staged archive and ingest "
        "each 3D file as its own Model, grouped under an auto-created Collection "
        "named after the archive."
    ),
)
async def select_archive_entries(
    archive_id: str,
    background_tasks: BackgroundTasks,
    req: ArchiveSelectRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    pending = importer.archives.get(archive_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="archive_not_found")
    if (
        pending.owner_user_id is not None
        and pending.owner_user_id != current_user.id
        and not current_user.is_superuser
    ):
        raise HTTPException(status_code=404, detail="archive_not_found")
    if not req.names and not req.entry_ids:
        raise HTTPException(status_code=400, detail="no_entries_selected")
    _require_ingest_collection(session, current_user, req.collection)
    pending = importer.archives.claim(archive_id)
    if pending is None:
        raise HTTPException(status_code=409, detail="archive_already_claimed")
    selected_names = list(req.names)
    if req.entry_ids:
        by_id = {entry.entry_id: entry.name for entry in pending.entries}
        try:
            selected_names.extend(by_id[entry_id] for entry_id in req.entry_ids)
        except KeyError as exc:
            raise HTTPException(
                status_code=400, detail="archive_entry_not_found"
            ) from exc

    auto_collection = importer.archive_collection_path(
        req.collection, pending.archive_name
    )
    try:
        staged_files = await run_in_threadpool(
            importer.extract_selected, pending.path, selected_names
        )
    except importer.ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        # The archive blob is no longer needed once entries are extracted.
        importer.archives.pop(archive_id)
        pending.path.unlink(missing_ok=True)

    if not staged_files:
        raise HTTPException(status_code=400, detail="no_importable_files")

    assert current_user.id is not None
    job_id = registry.create(owner_user_id=current_user.id, kind="archive")
    background_tasks.add_task(
        importer.import_assets,
        job_id=job_id,
        staged_files=staged_files,
        collection=auto_collection,
        tags=req.tags,
        source_url=pending.source_url,
        actor_user_id=current_user.id,
        session_factory=session_factory,
        nest_subdirs=True,
    )
    return IngestResponse(job_id=job_id, state="pending")


@router.post(
    "/url/files/{files_token}/select",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Import selected files from a multi-file model page",
    description=(
        "Download only the chosen files from a previously listed model page "
        "(see the model_files_manifest job result) and ingest each as its own Model."
    ),
)
async def select_model_files(
    files_token: str,
    background_tasks: BackgroundTasks,
    req: FileSelectRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    pending = ingest_background.pending_model_files.get(files_token)
    if pending is None or not _owns(pending.owner_user_id, current_user):
        raise HTTPException(status_code=404, detail="files_not_found")
    if not req.file_ids:
        raise HTTPException(status_code=400, detail="no_files_selected")
    chosen = [f for f in pending.files if f.file_id in set(req.file_ids)]
    if not chosen:
        raise HTTPException(status_code=400, detail="no_files_selected")
    _require_ingest_collection(session, current_user, req.collection)

    ingest_background.pending_model_files.pop(files_token)
    assert current_user.id is not None
    job_id = registry.create(owner_user_id=current_user.id, kind="url_selection")
    background_tasks.add_task(
        ingest_background.run_file_selection_import,
        job_id=job_id,
        page_url=pending.page_url,
        files=chosen,
        collection=req.collection,
        tags=req.tags,
        actor_user_id=current_user.id,
        session_factory=session_factory,
    )
    return IngestResponse(job_id=job_id, state="pending")


@router.post(
    "/collection/{collection_token}/select",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Import selected members from a reviewed collection",
    description=(
        "Import the chosen members of a previously resolved collection (see the "
        "collection_manifest job result) into the target collection."
    ),
)
async def select_collection_members(
    collection_token: str,
    background_tasks: BackgroundTasks,
    req: CollectionSelectRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    pending = ingest_background.pending_collections.get(collection_token)
    if pending is None or not _owns(pending.owner_user_id, current_user):
        raise HTTPException(status_code=404, detail="collection_not_found")
    if not req.member_ids:
        raise HTTPException(status_code=400, detail="no_members_selected")
    chosen = [m for m in pending.members if m.source_id in set(req.member_ids)]
    if not chosen:
        raise HTTPException(status_code=400, detail="no_members_selected")
    # The user already cleared the parent collection when the manifest was
    # created; re-check in case permissions changed, and allow an override parent.
    target = (
        ingest_background.collection_target(req.collection, pending.title)
        if req.collection
        else pending.target_collection
    )
    _require_ingest_collection(session, current_user, req.collection)

    ingest_background.pending_collections.pop(collection_token)
    assert current_user.id is not None
    job_id = registry.create(owner_user_id=current_user.id, kind="collection")
    background_tasks.add_task(
        ingest_background.run_collection_member_import,
        job_id=job_id,
        members=chosen,
        target_collection=target,
        tags=req.tags,
        actor_user_id=current_user.id,
        session_factory=session_factory,
        makerworld_cookie=pending.makerworld_cookie,
    )
    return IngestResponse(job_id=job_id, state="pending")


@router.get(
    "/jobs",
    response_model=list[IngestJobStatus],
    summary="List reconnectable ingestion jobs for the current user",
)
def list_jobs(
    response: Response,
    terminal_limit: int = Query(20, ge=0, le=100),
    tracked_job_id: list[str] = Query(default=[]),
    current_user: User = Depends(require_user),
) -> list[IngestJobStatus]:
    response.headers["Cache-Control"] = "no-store"
    if len(tracked_job_id) > 20:
        raise HTTPException(status_code=422, detail="too_many_tracked_job_ids")
    assert current_user.id is not None
    return registry.list_for_user(
        current_user.id,
        is_superuser=current_user.is_superuser,
        terminal_limit=terminal_limit,
        tracked_job_ids=tuple(dict.fromkeys(tracked_job_id)),
    )


@router.get(
    "/jobs/{job_id}",
    response_model=IngestJobStatus,
    summary="Get the status of an ingestion job",
)
def get_job(
    job_id: str,
    response: Response,
    current_user: User = Depends(require_user),
) -> IngestJobStatus:
    response.headers["Cache-Control"] = "no-store"
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
