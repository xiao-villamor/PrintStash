"""External library (NAS folder mirroring) management — superuser only.

Every endpoint is gated by the ``external_libraries_enabled`` opt-in switch; when
it is off the whole router responds 404 ``feature_disabled``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from croniter import croniter
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.core.config import settings
from app.core.http import get_or_404
from app.core.security import require_superuser
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    Collection,
    ExternalLibrary,
    ExternalLibraryCollectionMode,
    ExternalLibraryWatchMode,
    User,
)
from app.db.session import SessionFactory, get_session, get_session_factory
from app.schemas.ingest import IngestResponse
from app.services import external_library, runtime_config
from app.services.jobs import registry
from app.services.storage_paths import (
    StoragePathOverlapError,
    sqlite_database_path,
    validate_file_outside_roots,
    validate_path_outside_roots,
)

router = APIRouter(prefix="/libraries", tags=["external-libraries"])


def require_feature(session: Session = Depends(get_session)) -> None:
    """Block access unless NAS mirroring is enabled."""
    if not runtime_config.external_libraries_enabled(session):
        raise HTTPException(status_code=404, detail="feature_disabled")


class LibraryRead(BaseModel):
    id: int
    name: str
    root_path: str
    enabled: bool
    scan_interval_minutes: int
    scan_schedule: str
    watch_mode: ExternalLibraryWatchMode
    fs_kind: Optional[str]
    watch_active: bool
    collection_mode: ExternalLibraryCollectionMode
    target_collection_id: Optional[int]
    last_scanned_at: Optional[str]
    last_scan_status: Optional[str]
    last_scan_summary: Optional[dict]


class LibraryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    root_path: str = Field(min_length=1, max_length=1024)
    enabled: bool = True
    # Empty string = manual only. Otherwise a cron expression.
    scan_schedule: str = Field(default="0 * * * *", max_length=128)
    watch_mode: ExternalLibraryWatchMode = ExternalLibraryWatchMode.AUTO
    collection_mode: ExternalLibraryCollectionMode = (
        ExternalLibraryCollectionMode.MIRROR
    )
    target_collection_id: Optional[int] = None


class LibraryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    root_path: Optional[str] = Field(default=None, min_length=1, max_length=1024)
    enabled: Optional[bool] = None
    scan_schedule: Optional[str] = Field(default=None, max_length=128)
    watch_mode: Optional[ExternalLibraryWatchMode] = None
    collection_mode: Optional[ExternalLibraryCollectionMode] = None
    target_collection_id: Optional[int] = None


class LibraryPathScan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default="", max_length=1024)


def _validate_root_path(
    root_path: str, session: Session, *, exclude_library_id: int | None = None
) -> None:
    root = Path(root_path).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail="root_path_not_a_directory")
    if not os.access(root, os.R_OK):
        raise HTTPException(status_code=400, detail="root_path_unreadable")
    protected: dict[str, str | Path] = {
        "data_dir": settings.data_dir,
        "thumb_dir": settings.thumb_dir,
        "staging_dir": settings.staging_dir,
        "backup_dir": settings.backup_dir,
    }
    for library in session.exec(select(ExternalLibrary)).all():
        if library.id != exclude_library_id:
            protected[f"external_library_{library.id}"] = library.root_path
    try:
        validate_path_outside_roots(root, protected)
        database_path = sqlite_database_path(str(settings.db_url))
        if database_path is not None:
            validate_file_outside_roots(database_path, {"external_library": root})
        validate_file_outside_roots(
            settings.secrets_key_file, {"external_library": root}
        )
    except (OSError, RuntimeError, StoragePathOverlapError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="root_path_overlaps_managed_storage",
        ) from exc


def _validate_schedule(schedule: str) -> None:
    # Empty string is allowed and means "manual only".
    if schedule and not croniter.is_valid(schedule):
        raise HTTPException(status_code=400, detail="invalid_cron_schedule")


def _schedule_watcher_refresh(
    request: Request, background_tasks: BackgroundTasks
) -> None:
    """Reconcile the folder watcher after a config change (best-effort).

    The watcher only exists once the app lifespan has started; guard for setups
    (e.g. tests) where it isn't attached.
    """
    watcher = getattr(request.app.state, "library_watcher", None)
    if watcher is not None:
        background_tasks.add_task(watcher.refresh)


def _to_read(lib: ExternalLibrary) -> LibraryRead:
    summary = None
    if lib.last_scan_summary:
        try:
            summary = json.loads(lib.last_scan_summary)
        except (ValueError, TypeError):
            summary = None
    watch_active = lib.fs_kind is not None and external_library.should_watch(
        lib,
        lib.fs_kind,  # type: ignore[arg-type]
    )
    return LibraryRead(
        id=lib.id,  # type: ignore[arg-type]
        name=lib.name,
        root_path=lib.root_path,
        enabled=lib.enabled,
        scan_interval_minutes=lib.scan_interval_minutes,
        scan_schedule=lib.scan_schedule,
        watch_mode=lib.watch_mode,
        fs_kind=lib.fs_kind,
        watch_active=watch_active,
        collection_mode=lib.collection_mode,
        target_collection_id=lib.target_collection_id,
        last_scanned_at=lib.last_scanned_at.isoformat()
        if lib.last_scanned_at
        else None,
        last_scan_status=lib.last_scan_status.value if lib.last_scan_status else None,
        last_scan_summary=summary,
    )


@router.get(
    "",
    dependencies=[Depends(require_superuser), Depends(require_feature)],
    summary="List external (NAS) libraries",
)
def list_libraries(session: Session = Depends(get_session)) -> list[LibraryRead]:
    libs = session.exec(select(ExternalLibrary).order_by(ExternalLibrary.id)).all()
    return [_to_read(lib) for lib in libs]


def _require_target_collection(session: Session, collection_id: int | None) -> None:
    """Refuse a target collection that does not exist, before writing the row.

    `external_libraries.target_collection_id` is a foreign key, so an unknown id is a
    500 on a fresh installation and a dangling reference on one upgraded from an
    older release, which is missing the constraint. Neither is an answer to a bad
    request: 404 is.
    """
    if collection_id is None:
        return
    if session.get(Collection, collection_id) is None:
        raise HTTPException(status_code=404, detail="collection_not_found")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_superuser), Depends(require_feature)],
    summary="Create an external library",
)
def create_library(
    body: LibraryCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> LibraryRead:
    _validate_root_path(body.root_path, session)
    _validate_schedule(body.scan_schedule)
    _require_target_collection(session, body.target_collection_id)
    lib = ExternalLibrary(
        name=body.name.strip(),
        root_path=body.root_path,
        enabled=body.enabled,
        scan_schedule=body.scan_schedule,
        watch_mode=body.watch_mode,
        # Detect up front so watch_active is meaningful before the first scan.
        fs_kind=external_library.detect_fs_kind(body.root_path),
        collection_mode=body.collection_mode,
        target_collection_id=body.target_collection_id,
    )
    session.add(lib)
    session.commit()
    session.refresh(lib)
    _schedule_watcher_refresh(request, background_tasks)
    return _to_read(lib)


@router.patch(
    "/{library_id}",
    dependencies=[Depends(require_superuser), Depends(require_feature)],
    summary="Update an external library",
)
def update_library(
    library_id: int,
    body: LibraryUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> LibraryRead:
    lib = get_or_404(session, ExternalLibrary, library_id, "library_not_found")
    if body.root_path is not None and body.root_path != lib.root_path:
        _validate_root_path(body.root_path, session, exclude_library_id=lib.id)
        lib.root_path = body.root_path
        lib.fs_kind = external_library.detect_fs_kind(body.root_path)
    if body.name is not None:
        lib.name = body.name.strip()
    if body.enabled is not None:
        lib.enabled = body.enabled
    if body.scan_schedule is not None:
        _validate_schedule(body.scan_schedule)
        lib.scan_schedule = body.scan_schedule
    if body.watch_mode is not None:
        lib.watch_mode = body.watch_mode
    if body.collection_mode is not None:
        lib.collection_mode = body.collection_mode
    if body.target_collection_id is not None:
        _require_target_collection(session, body.target_collection_id)
        lib.target_collection_id = body.target_collection_id
    lib.updated_at = utcnow()
    session.add(lib)
    session.commit()
    session.refresh(lib)
    _schedule_watcher_refresh(request, background_tasks)
    return _to_read(lib)


@router.delete(
    "/{library_id}",
    dependencies=[Depends(require_superuser), Depends(require_feature)],
    summary="Remove an external library",
    description=(
        "Deletes the library and moves its indexed models/files to trash. The "
        "files on the NAS folder are never touched."
    ),
)
def delete_library(
    library_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict:
    lib = get_or_404(session, ExternalLibrary, library_id, "library_not_found")
    trashed = external_library.purge_library_index(session, library_id)
    session.delete(lib)
    session.commit()
    _schedule_watcher_refresh(request, background_tasks)
    return {"deleted": True, "files_trashed": trashed}


@router.post(
    "/{library_id}/scan",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_feature)],
    summary="Trigger a sync scan of the library now",
    description="Runs in the background; poll GET /ingest/jobs/{job_id} for progress.",
)
def scan_now(
    library_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    library = get_or_404(session, ExternalLibrary, library_id, "library_not_found")
    if (
        library.scan_claim_token
        and library.scan_claim_expires_at
        # SQLite hands a DateTime column back naive; ``utcnow()`` is aware.
        # Comparing them directly raised TypeError, which became a 500 on the
        # common case this branch exists to serve: a second scan request while
        # one is still running.
        and ensure_utc(library.scan_claim_expires_at) > utcnow()
        and library.scan_job_id
    ):
        return IngestResponse(
            job_id=library.scan_job_id,
            state="pending",
            message="library scan already queued",
        )
    job_id = registry.create(owner_user_id=current_user.id, kind="external_scan")
    background_tasks.add_task(
        external_library.scan_library,
        library_id,
        job_id=job_id,
        session_factory=session_factory,
    )
    return IngestResponse(job_id=job_id, state="pending", message="library scan queued")


@router.post(
    "/{library_id}/scan-path",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_feature)],
    summary="Import one folder under a configured library root",
)
def scan_path(
    library_id: int,
    body: LibraryPathScan,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    lib = get_or_404(session, ExternalLibrary, library_id, "library_not_found")
    root = Path(lib.root_path).resolve()
    candidate = (root / body.path).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=400, detail="path_outside_library_root")
    if not candidate.is_dir() or not os.access(candidate, os.R_OK):
        raise HTTPException(status_code=400, detail="path_missing_or_unreadable")
    job_id = registry.create(owner_user_id=current_user.id, kind="external_scan")
    background_tasks.add_task(
        external_library.scan_library,
        library_id,
        relative_path=body.path,
        job_id=job_id,
        session_factory=session_factory,
    )
    return IngestResponse(job_id=job_id, state="pending", message="folder scan queued")
