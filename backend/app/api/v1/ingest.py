"""Ingestion endpoints: accept bytes or a URL, record the intent, return a Job.

Every endpoint here does the same three things and nothing heavier: stage the
bytes (streamed and hashed on the way in), record an ``IngestRequest`` with its
queued Job and staging lease in one transaction, and nudge the job's
definition. Hashing into the vault, deduplication, parsing and thumbnails all
happen in background Jobs; poll ``GET /api/v1/jobs/{job_id}`` or subscribe to
``/api/v1/events/ws`` for progress.

These commands use synchronous SQL, storage and JobEngine ports. FastAPI runs
whole synchronous handlers in its bounded thread pool, keeping the event loop
free during acceptance as well as staging. Do not offload individual statements
from an async handler while passing its Session across execution boundaries.
"""

from __future__ import annotations

import hashlib
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi import (
    File as UploadFileParam,
)
from printstash_core.files import slugify
from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import OperationError
from app.core.logging import get_logger
from app.core.security import require_auth, require_user
from app.db.models import (
    SUFFIX_TO_FILE_TYPE,
    Collection,
    CollectionRole,
    FileRevisionStatus,
    IngestRequestKind,
    User,
)
from app.db.scopes import live
from app.db.session import get_session
from app.modules.identity import rbac
from app.modules.ingestion import background as ingest_background
from app.modules.ingestion import importer, orca, requests, staging_leases
from app.modules.storage import storage
from app.modules.work import nudge
from app.schemas.ingest import (
    ArchiveSelectRequest,
    CollectionSelectRequest,
    FileSelectRequest,
    UrlIngestRequest,
)
from app.schemas.jobs import JobAccepted

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
    from app.db.session import get_session_factory
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


def _accept_staged(
    session: Session,
    *,
    kind: IngestRequestKind,
    user: User,
    staged: Path,
    size: int,
    sha256: str,
    **columns,
) -> JobAccepted:
    """Record the request, its Job and its lease together; then nudge.

    Capacity is checked by the lease before anything is recorded. A request
    that cannot be recorded leaves no staged bytes behind.
    """
    assert user.id is not None
    try:
        request = requests.create(session, kind=kind, owner_user_id=user.id, **columns)
        staging_leases.create_job_lease(
            session,
            job_id=request.job_id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=size,
            sha256=sha256,
        )
        session.commit()
    except staging_leases.StagingCapacityExceeded as exc:
        session.rollback()
        staged.unlink(missing_ok=True)
        raise HTTPException(
            status_code=507, detail="staging_capacity_exceeded"
        ) from exc
    except Exception:
        session.rollback()
        staged.unlink(missing_ok=True)
        raise
    nudge(requests.DEFINITIONS[kind])
    return JobAccepted(job_id=request.job_id)


def _resolve_name(model_name: Optional[str], original_filename: str) -> str:
    """Return a non-empty display name, falling back to the file stem."""
    stem = Path(original_filename).stem
    return (model_name or stem).strip() or stem


def _orca_suffix(original_filename: str) -> str:
    """Preserve Orca's compound G-code container extension."""
    if original_filename.lower().endswith(".gcode.3mf"):
        return ".gcode.3mf"
    return Path(original_filename).suffix.lower() or ".gcode"


@router.post(
    "/orca",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Ingest a sliced G-code file from OrcaSlicer",
    description=(
        "Multipart upload from the OrcaSlicer post-processing hook. The G-code is "
        "attached as a Revision when native_context names one exact existing "
        "source Model. Unknown or ambiguous single-object sources are rejected; "
        "multi-object or unidentified exports remain standalone unless strict "
        "mapping is requested. Its slicer metadata and thumbnail follow as "
        "derivatives. Returns a job_id to poll via GET /api/v1/jobs/{job_id}."
    ),
)
def ingest_orca(
    file: UploadFile = UploadFileParam(..., description="The .gcode file"),
    model_name: Optional[str] = Form(None, description="Display name for the model"),
    collection: Optional[str] = Form(
        None, description="Optional collection, e.g. 'Functional/Brackets'"
    ),
    tags: Optional[str] = Form(None, description="Comma-separated tag list"),
    source_hash: Optional[str] = Form(
        None,
        description=(
            "Legacy source-mesh sha256. New hooks send native_context instead."
        ),
    ),
    native_context: Optional[str] = Form(
        None,
        description="Versioned normalized native OrcaSlicer context as JSON",
    ),
    strict_mapping: bool = Form(
        False,
        description="Reject exports that cannot attach to exactly one source Model",
    ),
    submission_id: Optional[str] = Form(
        None,
        description="Deterministic sha256 retry identity generated by the hook",
    ),
    revision_label: Optional[str] = Form(None, max_length=128),
    revision_status: Optional[FileRevisionStatus] = Form(None),
    revision_notes: Optional[str] = Form(None, max_length=4096),
    is_recommended: bool = Form(False),
    target_library_id: Optional[int] = Form(
        None,
        description="Write the blob into this external (NAS) library instead of vault",
    ),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> JobAccepted:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")

    original_filename = Path(file.filename).name
    suffix = _orca_suffix(original_filename)
    if suffix not in {*ingest_background.GCODE_SUFFIXES, ".gcode.3mf"}:
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    if source_hash and (
        len(source_hash) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in source_hash)
    ):
        raise HTTPException(status_code=422, detail="source_hash_invalid")
    try:
        context = orca.parse_native_context(native_context)
    except orca.OrcaContextError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc

    resolved_source_hash = source_hash
    attached_to_source = False
    if context is None:
        if strict_mapping:
            raise HTTPException(status_code=422, detail="orca_source_metadata_required")
        selection: dict[str, object] = {}
    else:
        resolution = orca.resolve_source_model(session, current_user, context)
        if resolution.status == "matched":
            assert resolution.model is not None
            if source_hash and source_hash.lower() != resolution.model.hash.lower():
                raise HTTPException(
                    status_code=422, detail="orca_source_identity_conflict"
                )
            resolved_source_hash = resolution.model.hash
            attached_to_source = True
        elif (
            resolution.status == "not_found"
            and context.classification == "single_object"
        ):
            raise HTTPException(status_code=404, detail="orca_source_not_found")
        elif resolution.status == "ambiguous":
            raise HTTPException(status_code=409, detail="orca_source_ambiguous")
        elif strict_mapping:
            detail = (
                "orca_multi_object_not_attachable"
                if context.classification == "multi_object"
                else "orca_source_metadata_required"
            )
            raise HTTPException(status_code=422, detail=detail)
        if source_hash and not attached_to_source:
            raise HTTPException(status_code=422, detail="orca_source_identity_conflict")
        selection = {"native_context": orca.stored_context(context)}
        if submission_id is not None:
            assert current_user.id is not None
            try:
                selection["ingestion_key"] = orca.ingestion_key(
                    current_user.id, submission_id, context
                )
            except orca.OrcaContextError as exc:
                raise HTTPException(status_code=422, detail=exc.code) from exc
    if submission_id is not None and context is None:
        raise HTTPException(status_code=422, detail="orca_native_context_required")
    selection.update(
        {
            "revision_label": revision_label,
            "revision_status": (
                revision_status.value
                if revision_status is not None
                else FileRevisionStatus.NEEDS_TEST.value
                if attached_to_source
                else None
            ),
            "revision_notes": revision_notes,
            "is_recommended": is_recommended,
        }
    )
    _require_ingest_collection(session, current_user, collection)
    _validate_target_library(session, target_library_id)

    staged, staged_size, staged_hash = _stage_upload(file, suffix)
    return _accept_staged(
        session,
        kind=IngestRequestKind.UPLOAD,
        user=current_user,
        staged=staged,
        size=staged_size,
        sha256=staged_hash,
        original_filename=original_filename,
        model_name=orca.display_name(context, model_name, original_filename),
        collection=collection,
        tags=tags,
        source_hash=resolved_source_hash,
        file_type="gcode",
        target_library_id=target_library_id,
        selection=selection,
    )


@router.post(
    "/model",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Ingest a source file (STL, 3MF, OBJ, STEP, DXF)",
    description=(
        "Multipart upload of a source file. The file is staged and committed as "
        "an Artifact by a background Job; geometry and a rendered thumbnail follow "
        "as derivatives where supported. Returns a job_id you can poll via "
        "GET /api/v1/jobs/{job_id}."
    ),
)
def ingest_model(
    file: UploadFile = UploadFileParam(
        ..., description="A .stl, .3mf, .obj, .step, .stp, or .dxf file"
    ),
    model_name: Optional[str] = Form(None, description="Display name for the model"),
    collection: Optional[str] = Form(
        None, description="Optional collection, e.g. 'Functional/Brackets'"
    ),
    tags: Optional[str] = Form(None, description="Comma-separated tag list"),
    target_library_id: Optional[int] = Form(
        None,
        description="Write the blob into this external (NAS) library instead of vault",
    ),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> JobAccepted:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")

    original_filename = Path(file.filename).name
    suffix = Path(original_filename).suffix.lower()
    if suffix not in ingest_background.MESH_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    _require_ingest_collection(session, current_user, collection)
    _validate_target_library(session, target_library_id)

    staged, staged_size, staged_hash = _stage_upload(file, suffix)
    return _accept_staged(
        session,
        kind=IngestRequestKind.UPLOAD,
        user=current_user,
        staged=staged,
        size=staged_size,
        sha256=staged_hash,
        original_filename=original_filename,
        model_name=_resolve_name(model_name, original_filename),
        collection=collection,
        tags=tags,
        file_type=SUFFIX_TO_FILE_TYPE[suffix].value,
        target_library_id=target_library_id,
    )


@router.post(
    "/url",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Import a model from a direct file, a model page, a collection or a .zip URL",
    description=(
        "Records the URL for a background Job that downloads it server-side "
        "(SSRF-guarded). A direct mesh/G-code URL is imported; a .zip, a multi-file "
        "model page or a collection in review mode ends the Job with a manifest "
        "whose token (the Job id) selects what to import."
    ),
)
def ingest_url(
    req: UrlIngestRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> JobAccepted:
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="url_required")
    try:
        importer.validate_public_url(req.url.strip())
    except importer.ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _require_ingest_collection(session, current_user, req.collection)

    assert current_user.id is not None
    request = requests.create(
        session,
        kind=IngestRequestKind.URL,
        owner_user_id=current_user.id,
        selection={"review": req.review},
        credential=req.thingiverse_cookie,
        source_url=req.url.strip(),
        collection=req.collection,
        tags=req.tags,
    )
    session.commit()
    nudge(requests.DEFINITIONS[IngestRequestKind.URL])
    return JobAccepted(job_id=request.job_id)


@router.post(
    "/archive/inspect",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Stage a ZIP archive and list its importable entries in the background",
    description=(
        "The archive is staged under a lease owned by the returned Job, which "
        "inspects it (zip-slip/zip-bomb guarded) and ends with an archive manifest. "
        "Select entries with POST /ingest/archive/{archive_id}/select, where "
        "archive_id is the manifest's archive_id (the Job id)."
    ),
)
def inspect_archive_background(
    file: UploadFile = UploadFileParam(..., description="The .zip archive"),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> JobAccepted:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename_required")
    original_filename = Path(file.filename).name
    if Path(original_filename).suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    staged, staged_size, staged_hash = _stage_upload(file, ".zip")
    if not zipfile.is_zipfile(staged):
        staged.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="archive_invalid")
    accepted = _accept_staged(
        session,
        kind=IngestRequestKind.ARCHIVE_INSPECT,
        user=current_user,
        staged=staged,
        size=staged_size,
        sha256=staged_hash,
        original_filename=original_filename,
    )
    accepted.message = "archive inspection queued"
    return accepted


@router.post(
    "/archive/{archive_id}/select",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Import selected entries from an inspected archive",
    description=(
        "Hands the inspected archive to a new Job that extracts the chosen entries "
        "and ingests each 3D file as its own Model, grouped under an auto-created "
        "Collection named after the archive."
    ),
)
def select_archive_entries(
    archive_id: str,
    req: ArchiveSelectRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> JobAccepted:
    manifest_request, manifest = requests.manifest_for(
        session, archive_id, kind="archive", user=current_user
    )
    if not req.names and not req.entry_ids:
        raise HTTPException(status_code=400, detail="no_entries_selected")
    _require_ingest_collection(session, current_user, req.collection)
    entries = list(manifest.get("entries", []))
    selected_names = list(req.names)
    if req.entry_ids:
        by_id = {entry["entry_id"]: entry["name"] for entry in entries}
        try:
            selected_names.extend(by_id[entry_id] for entry_id in req.entry_ids)
        except KeyError as exc:
            raise HTTPException(
                status_code=400, detail="archive_entry_not_found"
            ) from exc
    known = {entry["name"] for entry in entries if entry.get("file_type")}
    chosen = [name for name in dict.fromkeys(selected_names) if name in known]
    if not chosen:
        raise HTTPException(status_code=400, detail="no_importable_files")

    assert current_user.id is not None
    request = requests.create(
        session,
        kind=IngestRequestKind.ARCHIVE_SELECTION,
        owner_user_id=current_user.id,
        selection={"names": chosen, "archive_name": manifest.get("archive_name")},
        collection=req.collection,
        tags=req.tags,
        source_url=manifest.get("source_url"),
    )
    try:
        staging_leases.transfer_job_leases(
            session, from_job_id=manifest_request.job_id, to_job_id=request.job_id
        )
    except staging_leases.StagingLeaseError as exc:
        session.rollback()
        raise HTTPException(status_code=410, detail="archive_expired") from exc
    requests.claim_manifest(session, manifest_request)
    session.commit()
    nudge(requests.DEFINITIONS[IngestRequestKind.ARCHIVE_SELECTION])
    return JobAccepted(job_id=request.job_id)


@router.post(
    "/url/files/{files_token}/select",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Import selected files from a multi-file model page",
    description=(
        "Downloads only the chosen files from a previously listed model page "
        "(see the model_files_manifest job result) and ingests each as its own Model."
    ),
)
def select_model_files(
    files_token: str,
    req: FileSelectRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> JobAccepted:
    try:
        _source, manifest = requests.manifest_for(
            session, files_token, kind="model_files", user=current_user
        )
    except OperationError as exc:
        raise HTTPException(status_code=404, detail="files_not_found") from exc
    if not req.file_ids:
        raise HTTPException(status_code=400, detail="no_files_selected")
    wanted = set(req.file_ids)
    chosen = [f for f in manifest.get("files", []) if f.get("file_id") in wanted]
    if not chosen:
        raise HTTPException(status_code=400, detail="no_files_selected")
    _require_ingest_collection(session, current_user, req.collection)

    assert current_user.id is not None
    request = requests.create(
        session,
        kind=IngestRequestKind.URL_SELECTION,
        owner_user_id=current_user.id,
        selection={"files": chosen},
        source_url=manifest.get("page_url"),
        collection=req.collection,
        tags=req.tags,
    )
    requests.claim_manifest(session, _source)
    session.commit()
    nudge(requests.DEFINITIONS[IngestRequestKind.URL_SELECTION])
    return JobAccepted(job_id=request.job_id)


@router.post(
    "/collection/{collection_token}/select",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_auth)],
    summary="Import selected members from a reviewed collection",
    description=(
        "Imports the chosen members of a previously resolved collection (see the "
        "collection_manifest job result) into the target collection."
    ),
)
def select_collection_members(
    collection_token: str,
    req: CollectionSelectRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> JobAccepted:
    try:
        source, manifest = requests.manifest_for(
            session, collection_token, kind="collection", user=current_user
        )
    except OperationError as exc:
        raise HTTPException(status_code=404, detail="collection_not_found") from exc
    if not req.member_ids:
        raise HTTPException(status_code=400, detail="no_members_selected")
    wanted = set(req.member_ids)
    chosen = [m for m in manifest.get("members", []) if m.get("source_id") in wanted]
    if not chosen:
        raise HTTPException(status_code=400, detail="no_members_selected")
    # The user cleared the parent collection when the manifest was created;
    # re-check in case permissions changed, and allow an override parent.
    target = (
        ingest_background.collection_target(req.collection, manifest.get("title", ""))
        if req.collection
        else str(manifest.get("target_collection") or "")
    )
    _require_ingest_collection(session, current_user, req.collection)

    assert current_user.id is not None
    request = requests.create(
        session,
        kind=IngestRequestKind.COLLECTION,
        owner_user_id=current_user.id,
        selection={"members": chosen, "target_collection": target},
        tags=req.tags,
    )
    requests.claim_manifest(session, source)
    session.commit()
    nudge(requests.DEFINITIONS[IngestRequestKind.COLLECTION])
    return JobAccepted(job_id=request.job_id)
