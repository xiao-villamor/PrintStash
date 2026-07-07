"""Ingestion endpoints: OrcaSlicer G-code + mesh uploads."""

from __future__ import annotations

import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, Optional, TypeVar

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
from app.schemas.ingest import (
    ArchiveEntryRead,
    ArchiveManifest,
    ArchiveSelectRequest,
    CollectionManifest,
    CollectionMemberRead,
    CollectionSelectRequest,
    FileSelectRequest,
    IngestJobStatus,
    IngestResponse,
    ModelFileRead,
    ModelFilesManifest,
    UrlIngestRequest,
)
from app.services import import_resolvers, importer, rbac, storage, taxonomy
from app.services.ingestion import ingest_mesh, ingest_orca_gcode
from app.services.jobs import registry

logger = get_logger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

_GCODE_SUFFIXES = {".gcode", ".g", ".gco", ".bgcode"}
_MESH_SUFFIXES = {".stl", ".3mf", ".obj", ".step", ".stp"}


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


def _validate_target_library(session: Session, library_id: int | None) -> None:
    """Reject a write-back target unless mirroring is on and the library exists."""
    if library_id is None:
        return
    from app.db.models import ExternalLibrary
    from app.services.runtime_config import external_libraries_enabled

    if not external_libraries_enabled(session):
        raise HTTPException(status_code=400, detail="external_libraries_disabled")
    library = session.get(ExternalLibrary, library_id)
    if library is None or not library.enabled:
        raise HTTPException(status_code=400, detail="library_not_found")


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


# --------------------------------------------------------------------------- #
# Pending manifests for the two-step review flows (per-file + collection).
# Unlike the archive registry these hold only metadata (nothing is downloaded
# until the user selects), so there is no staged blob to clean up on prune.
# --------------------------------------------------------------------------- #
@dataclass
class _PendingModelFiles:
    page_url: str
    page_title: str
    owner_user_id: Optional[int]
    files: list[import_resolvers.ModelFile]
    created_at: float = field(default_factory=time.time)


@dataclass
class _PendingCollection:
    title: str
    target_collection: str
    owner_user_id: Optional[int]
    members: list[import_resolvers.CollectionMember]
    # The session cookie resolved when the manifest was created, carried forward
    # so the later /select confirm can still authenticate downloads. May be stale
    # by confirm time (sessions expire within the 1h TTL) — then downloads 403.
    makerworld_cookie: Optional[str] = None
    created_at: float = field(default_factory=time.time)


T = TypeVar("T")


class _PendingRegistry(Generic[T]):
    """In-process token store for review manifests (1h TTL)."""

    _TTL = 3600.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, T] = {}

    def add(self, pending: T) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._prune()
            self._items[token] = pending
        return token

    def get(self, token: str) -> Optional[T]:
        with self._lock:
            return self._items.get(token)

    def pop(self, token: str) -> Optional[T]:
        with self._lock:
            return self._items.pop(token, None)

    def _prune(self) -> None:
        cutoff = time.time() - self._TTL
        for key in [
            k for k, v in self._items.items() if getattr(v, "created_at", 0) < cutoff
        ]:
            self._items.pop(key, None)


pending_model_files: _PendingRegistry[_PendingModelFiles] = _PendingRegistry()
pending_collections: _PendingRegistry[_PendingCollection] = _PendingRegistry()


def _makerworld_cookie(override: Optional[str]) -> Optional[str]:
    """Effective MakerWorld session cookie for an import.

    Prefers a per-request ``override``, falls back to the instance-level
    ``settings.makerworld_cookie`` (admin pastes it once so end users paste
    nothing), and is ``None`` when neither is set — anonymous imports can list a
    collection but MakerWorld's auth-gated download endpoints will then 403.
    """
    return (override or "").strip() or settings.makerworld_cookie.strip() or None


def _collection_target(parent: Optional[str], title: str) -> str:
    """Nest a collection named after the source under the user's chosen parent."""
    base = (title or "").strip() or "Imported collection"
    if parent and parent.strip():
        return f"{parent.strip().rstrip('/')}/{base}"
    return base


def _owns(owner_user_id: Optional[int], user: User) -> bool:
    return owner_user_id is None or owner_user_id == user.id or user.is_superuser


async def _download_and_collect(download_url: str) -> list[tuple[Path, str]]:
    """Download one direct link; if it is a zip, extract every importable entry.

    Returns the staged ``(path, filename)`` tuples ready for ingestion (empty if
    the link is neither a model file nor an archive with importable entries).
    """
    staged, original_filename = await importer.download_to_staging(download_url)
    suffix = Path(original_filename).suffix.lower()
    if suffix == ".zip" or (
        zipfile.is_zipfile(staged)
        and suffix not in _MESH_SUFFIXES
        and suffix not in _GCODE_SUFFIXES
    ):
        try:
            entries = await run_in_threadpool(importer.inspect_archive, staged)
            names = [e.name for e in entries if e.file_type]
            extracted = await run_in_threadpool(
                importer.extract_selected, staged, names
            )
        finally:
            staged.unlink(missing_ok=True)
        return extracted
    if suffix not in _MESH_SUFFIXES and suffix not in _GCODE_SUFFIXES:
        staged.unlink(missing_ok=True)
        return []
    return [(staged, original_filename)]


async def _stage_members(
    members: list[import_resolvers.CollectionMember],
    *,
    makerworld_cookie: Optional[str],
) -> list[importer.ResolvedGroup]:
    """Resolve + download every collection member, isolating per-member failures."""
    groups: list[importer.ResolvedGroup] = []
    for member in members:
        group = importer.ResolvedGroup(source_url=member.page_url, title=member.title)
        try:
            link = (
                await import_resolvers.resolve_page_url(
                    member.page_url, makerworld_cookie=makerworld_cookie
                )
                or member.page_url
            )
            group.staged_files = await _download_and_collect(link)
            if not group.staged_files:
                group.error = "no_importable_files"
        except importer.ImportError_ as exc:
            group.error = str(exc)
        except Exception as exc:  # noqa: BLE001 — per-member boundary; continue
            logger.exception("collection member failed: %s", member.page_url)
            group.error = str(exc)
        groups.append(group)
    return groups


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
    if suffix not in _GCODE_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    _require_ingest_collection(session, current_user, collection)
    _validate_target_library(session, target_library_id)

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
    if suffix not in _MESH_SUFFIXES:
        raise HTTPException(status_code=400, detail="unsupported_file_type")
    _require_ingest_collection(session, current_user, collection)
    _validate_target_library(session, target_library_id)

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
        target_library_id=target_library_id,
    )
    return IngestResponse(job_id=job_id, state="pending")


def _manifest_from_pending(archive_id: str, pending: "importer._PendingArchive") -> ArchiveManifest:
    return ArchiveManifest(
        archive_id=archive_id,
        archive_name=pending.archive_name,
        entries=[
            ArchiveEntryRead(
                name=e.name,
                size_bytes=e.size_bytes,
                file_type=e.file_type,
                is_image=e.is_image,
            )
            for e in pending.entries
        ],
    )


def _stage_model_files_manifest(
    job_id: str,
    req: UrlIngestRequest,
    actor_user_id: int,
    listing: tuple[str, list[import_resolvers.ModelFile]],
) -> None:
    """Stash a multi-file model page and report a manifest job result."""
    page_title, files = listing
    token = pending_model_files.add(
        _PendingModelFiles(
            page_url=req.url,
            page_title=page_title,
            owner_user_id=actor_user_id,
            files=files,
        )
    )
    manifest = ModelFilesManifest(
        files_token=token,
        page_title=page_title,
        files=[
            ModelFileRead(file_id=f.file_id, name=f.name, file_type=f.file_type, size=f.size)
            for f in files
        ],
    )
    registry.update(
        job_id,
        state="completed",
        result={
            "kind": "model_files_manifest",
            **manifest.model_dump(),
            "collection": req.collection,
        },
    )


async def _handle_collection_url(
    *,
    job_id: str,
    req: UrlIngestRequest,
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Resolve a collection URL; either stage a review manifest or import all."""
    cookie = _makerworld_cookie(req.makerworld_cookie)
    resolved = await import_resolvers.resolve_collection_url(
        req.url, makerworld_cookie=cookie
    )
    if not resolved:
        registry.update(job_id, state="failed", error="collection_resolve_failed")
        return
    title, members = resolved
    target = _collection_target(req.collection, title)

    if req.review:
        token = pending_collections.add(
            _PendingCollection(
                title=title,
                target_collection=target,
                owner_user_id=actor_user_id,
                members=members,
                makerworld_cookie=cookie,
            )
        )
        manifest = CollectionManifest(
            collection_token=token,
            collection_name=title,
            target_collection=target,
            members=[
                CollectionMemberRead(source_id=m.source_id, title=m.title, page_url=m.page_url)
                for m in members
            ],
        )
        registry.update(
            job_id,
            state="completed",
            result={"kind": "collection_manifest", **manifest.model_dump()},
        )
        return

    groups = await _stage_members(members, makerworld_cookie=cookie)
    await run_in_threadpool(
        importer.import_resolved_groups,
        job_id=job_id,
        groups=groups,
        collection=target,
        tags=req.tags,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
    )


async def _import_from_url(
    *,
    job_id: str,
    req: UrlIngestRequest,
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Background task: download a URL, then ingest it or stage it as an archive.

    If ``req.url`` is a collection, it fans out into many models (auto or review);
    a multi-file Printables page returns a file-selection manifest; otherwise a
    model *page* is resolved to a direct download link (the user-pasted page URL
    is still recorded as the model's ``source_url``).
    """
    try:
        registry.update(job_id, state="running")
        if import_resolvers.classify_collection(req.url):
            await _handle_collection_url(
                job_id=job_id,
                req=req,
                actor_user_id=actor_user_id,
                session_factory=session_factory,
            )
            return
        # A Printables page with more than one file → let the user pick which.
        listing = await import_resolvers.list_model_files(req.url)
        if listing is not None and len(listing[1]) > 1:
            _stage_model_files_manifest(job_id, req, actor_user_id, listing)
            return
        download_url = (
            await import_resolvers.resolve_page_url(
                req.url,
                makerworld_cookie=_makerworld_cookie(req.makerworld_cookie),
                thingiverse_cookie=req.thingiverse_cookie,
            )
            or req.url
        )
        staged, original_filename = await importer.download_to_staging(download_url)
    except importer.ImportError_ as exc:
        registry.update(job_id, state="failed", error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — network/IO boundary
        logger.exception("url import download failed: %s", req.url)
        registry.update(job_id, state="failed", error=str(exc))
        return

    suffix = Path(original_filename).suffix.lower()
    # Treat anything that is actually a zip as an archive (handles missing/odd
    # extensions on direct download links). A .3mf is itself a zip container but
    # is a single model, so route it (and other known mesh/g-code suffixes) to
    # direct ingestion rather than the archive-manifest flow.
    if suffix == ".zip" or (
        zipfile.is_zipfile(staged)
        and suffix not in _MESH_SUFFIXES
        and suffix not in _GCODE_SUFFIXES
    ):
        try:
            entries = await run_in_threadpool(importer.inspect_archive, staged)
        except importer.ImportError_ as exc:
            staged.unlink(missing_ok=True)
            registry.update(job_id, state="failed", error=str(exc))
            return
        pending = importer._PendingArchive(
            path=staged,
            archive_name=original_filename,
            owner_user_id=actor_user_id,
            entries=entries,
            source_url=req.url,
        )
        archive_id = importer.archives.add(pending)
        manifest = _manifest_from_pending(archive_id, pending)
        registry.update(
            job_id,
            state="completed",
            result={"kind": "archive_manifest", **manifest.model_dump()},
        )
        return

    if suffix not in _MESH_SUFFIXES and suffix not in _GCODE_SUFFIXES:
        # The URL resolved to something that isn't a model file or a .zip —
        # almost always a model *page* (HTML) rather than a direct download
        # link. Use a dedicated code so the UI can tell the user what to paste.
        staged.unlink(missing_ok=True)
        registry.update(job_id, state="failed", error="url_not_a_direct_file")
        return

    # Single direct file — ingest under the user's chosen collection. Offload
    # the (blocking, CPU-heavy) pipeline so the event loop stays free.
    await run_in_threadpool(
        importer.import_assets,
        job_id=job_id,
        staged_files=[(staged, original_filename)],
        collection=req.collection,
        tags=req.tags,
        source_url=req.url,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
    )


async def _run_file_selection_import(
    *,
    job_id: str,
    page_url: str,
    files: list[import_resolvers.ModelFile],
    collection: Optional[str],
    tags: Optional[str],
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Background task: download a chosen subset of a page's files and ingest them."""
    try:
        registry.update(job_id, state="running")
        links = await import_resolvers.resolve_selected_download(page_url, files)
        staged_files: list[tuple[Path, str]] = []
        for link in links:
            staged_files.extend(await _download_and_collect(link))
    except importer.ImportError_ as exc:
        registry.update(job_id, state="failed", error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — network/IO boundary
        logger.exception("file selection import failed: %s", page_url)
        registry.update(job_id, state="failed", error=str(exc))
        return
    if not staged_files:
        registry.update(job_id, state="failed", error="no_importable_files")
        return
    await run_in_threadpool(
        importer.import_assets,
        job_id=job_id,
        staged_files=staged_files,
        collection=collection,
        tags=tags,
        source_url=page_url,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
    )


async def _run_collection_member_import(
    *,
    job_id: str,
    members: list[import_resolvers.CollectionMember],
    target_collection: str,
    tags: Optional[str],
    actor_user_id: int,
    session_factory: SessionFactory,
    makerworld_cookie: Optional[str] = None,
) -> None:
    """Background task: stage selected collection members and ingest them."""
    try:
        registry.update(job_id, state="running")
        groups = await _stage_members(members, makerworld_cookie=makerworld_cookie)
    except Exception as exc:  # noqa: BLE001 — network/IO boundary
        logger.exception("collection member import failed")
        registry.update(job_id, state="failed", error=str(exc))
        return
    await run_in_threadpool(
        importer.import_resolved_groups,
        job_id=job_id,
        groups=groups,
        collection=target_collection,
        tags=tags,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
    )


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
    job_id = registry.create(owner_user_id=current_user.id)
    background_tasks.add_task(
        _import_from_url,
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

    staged = await run_in_threadpool(_stage_upload, file, ".zip")
    if not zipfile.is_zipfile(staged):
        staged.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="archive_invalid")
    try:
        entries = await run_in_threadpool(importer.inspect_archive, staged)
    except importer.ImportError_ as exc:
        staged.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pending = importer._PendingArchive(
        path=staged,
        archive_name=original_filename,
        owner_user_id=current_user.id,
        entries=entries,
    )
    archive_id = importer.archives.add(pending)
    return _manifest_from_pending(archive_id, pending)


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
    if not req.names:
        raise HTTPException(status_code=400, detail="no_entries_selected")
    _require_ingest_collection(session, current_user, req.collection)

    auto_collection = importer._collection_for_archive(
        req.collection, pending.archive_name
    )
    try:
        staged_files = await run_in_threadpool(
            importer.extract_selected, pending.path, req.names
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
    job_id = registry.create(owner_user_id=current_user.id)
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
    pending = pending_model_files.get(files_token)
    if pending is None or not _owns(pending.owner_user_id, current_user):
        raise HTTPException(status_code=404, detail="files_not_found")
    if not req.file_ids:
        raise HTTPException(status_code=400, detail="no_files_selected")
    chosen = [f for f in pending.files if f.file_id in set(req.file_ids)]
    if not chosen:
        raise HTTPException(status_code=400, detail="no_files_selected")
    _require_ingest_collection(session, current_user, req.collection)

    pending_model_files.pop(files_token)
    assert current_user.id is not None
    job_id = registry.create(owner_user_id=current_user.id)
    background_tasks.add_task(
        _run_file_selection_import,
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
    pending = pending_collections.get(collection_token)
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
        _collection_target(req.collection, pending.title)
        if req.collection
        else pending.target_collection
    )
    _require_ingest_collection(session, current_user, req.collection)

    pending_collections.pop(collection_token)
    assert current_user.id is not None
    job_id = registry.create(owner_user_id=current_user.id)
    background_tasks.add_task(
        _run_collection_member_import,
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
