"""Backup & restore endpoints."""

from __future__ import annotations

import tarfile
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import Session

import app.modules.backups.backup.adoption as backup_adoption
import app.modules.backups.backup.caches as backup_caches
import app.modules.backups.backup.catalogue as backup_catalogue
import app.modules.backups.backup.contracts as backup_contracts
import app.modules.backups.backup.creation as backup_creation
import app.modules.backups.backup.deletion as backup_deletion
import app.modules.backups.backup.restore as backup_restore
import app.modules.backups.backup.snapshot as backup_snapshot
import app.modules.backups.backup.verification as backup_verification
import app.runtime.maintenance as backup_maintenance
from app.core.errors import OperationError
from app.core.logging import get_logger
from app.core.security import require_superuser
from app.db.session import get_session
from app.modules.backups.backup_capabilities import backup_operations
from app.modules.backups.backup_catalogue import BackupIdentityConflictError
from app.modules.backups.queries import source_view
from app.modules.storage.storage import UploadTooLarge

logger = get_logger(__name__)

router = APIRouter(prefix="/backups", tags=["backups"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_superuser)],
    summary="Create a new vault backup",
    description=(
        "Creates a full backup (database + all stored files) as a tar.gz "
        "archive. Runs synchronously — large vaults may take a while. "
        "Returns the backup metadata."
    ),
)
def create_backup(
    background_tasks: BackgroundTasks,
) -> dict:
    try:
        meta = backup_creation.create_backup()
    except backup_contracts.DatabaseBackupNotSupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        detail = str(exc)
        if detail == "backup_destination_required":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            ) from exc
        if detail == "backup_all_destinations_failed":
            return JSONResponse(
                status_code=status.HTTP_502_BAD_GATEWAY,
                content={"detail": detail, "run_id": getattr(exc, "run_id", None)},
            )
        raise
    background_tasks.add_task(backup_deletion.purge_old_backups)
    return {
        "run_id": meta.run_id,
        "outcome": meta.outcome,
        "destination_results": meta.destination_results,
        "backup_id": meta.id,
        "created_at": meta.created_at,
        "size_bytes": meta.size_bytes,
        "file_count": meta.file_count,
        "storage_backend": meta.storage_backend,
        "app_version": meta.app_version,
        "location": meta.location,
        "archive_sha256": meta.archive_sha256,
        "source_ref": meta.source_ref,
        "provider_ref": meta.provider_ref,
        "namespace": meta.namespace,
    }


@router.get(
    "/runs",
    dependencies=[Depends(require_superuser)],
    summary="List backup execution results",
)
def list_backup_runs(
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
) -> list[dict]:
    from app.modules.backups.backup_runs import list_runs

    return list_runs(limit=limit, offset=offset)


@router.get(
    "/runs/{run_id}",
    dependencies=[Depends(require_superuser)],
    summary="Inspect one backup execution",
)
def get_backup_run(run_id: str) -> dict:
    from app.modules.backups.backup_runs import reconcile_interrupted_runs, run_detail

    reconcile_interrupted_runs()
    try:
        return run_detail(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="backup_run_not_found") from exc


@router.post(
    "/runs/destinations/{result_id}/retry",
    dependencies=[Depends(require_superuser)],
    summary="Retry one exact failed backup destination",
)
def retry_backup_destination(result_id: str) -> dict:
    from app.modules.backups.backup_replica_retry import RetryRefused
    from app.modules.backups.retry_commands import retry_destination

    try:
        return retry_destination(result_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=404, detail="backup_destination_result_not_found"
        ) from exc
    except RetryRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_superuser)],
    summary="Upload an existing backup archive",
)
def upload_backup(file: UploadFile = File(...)) -> dict:
    try:
        meta = backup_adoption.upload_backup_archive(
            file.filename or "", file.file, expected_size=file.size
        )
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="upload_too_large",
        ) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="backup_already_exists") from exc
    except (ValueError, RuntimeError, OSError, tarfile.TarError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "backup_id": meta.id,
        "created_at": meta.created_at,
        "size_bytes": meta.size_bytes,
        "file_count": meta.file_count,
        "storage_backend": meta.storage_backend,
        "app_version": meta.app_version,
        "location": meta.location,
        "archive_sha256": meta.archive_sha256,
        "source_ref": meta.source_ref,
        "provider_ref": meta.provider_ref,
        "namespace": meta.namespace,
    }


@router.get(
    "",
    dependencies=[Depends(require_superuser)],
    summary="List available backups",
    description="Returns backups from local storage and (if configured) cloud storage, merged and deduplicated.",
)
def list_backups() -> list[dict]:
    metas = backup_catalogue.list_backups()
    return [source_view(meta) for meta in metas]


@router.get(
    "/sources",
    dependencies=[Depends(require_superuser)],
    summary="List every exact backup source",
)
def list_backup_sources() -> list[dict]:
    """Expose replicas and collision candidates without collapsing locators."""
    metas = backup_catalogue.list_backup_sources()
    return [source_view(meta) for meta in metas]


@router.post(
    "/adopt-local",
    dependencies=[Depends(require_superuser)],
    summary="Adopt a legacy local backup",
    description=(
        "Validate and register one unowned legacy archive. Archives are not "
        "auto-adopted during listing; the filename must be in the configured "
        "backup directory."
    ),
)
def adopt_local_backup(filename: str) -> dict:
    try:
        meta = backup_adoption.adopt_local_backup(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "backup_id": meta.id,
        "created_at": meta.created_at,
        "size_bytes": meta.size_bytes,
        "file_count": meta.file_count,
        "storage_backend": meta.storage_backend,
        "app_version": meta.app_version,
        "location": meta.location,
        "archive_sha256": meta.archive_sha256,
        "source_ref": meta.source_ref,
        "provider_ref": meta.provider_ref,
        "namespace": meta.namespace,
        "operations": backup_operations(meta),
    }


@router.get(
    "/unowned-local",
    dependencies=[Depends(require_superuser)],
    summary="Discover valid unowned legacy local backups",
)
def discover_unowned_local_backups() -> list[dict[str, object]]:
    return backup_adoption.discover_unowned_local_backups()


@router.get(
    "/unowned-s3",
    dependencies=[Depends(require_superuser)],
    summary="Discover valid unowned legacy S3 backups",
)
def discover_unowned_s3_backups() -> list[dict[str, object]]:
    return backup_adoption.discover_unowned_s3_backups()


@router.get(
    "/unowned-remote",
    dependencies=[Depends(require_superuser)],
    summary="Discover valid unowned OpenDAL backups",
)
def discover_unowned_remote_backups() -> list[dict[str, object]]:
    return backup_adoption.discover_unowned_opendal_backups()


@router.post(
    "/adopt-s3",
    dependencies=[Depends(require_superuser)],
    summary="Adopt one legacy S3 backup",
)
def adopt_s3_backup(
    key: str = Query(..., min_length=1),
    source_ref: str = Query(..., min_length=1),
    expected_archive_sha256: str = Query(..., min_length=64, max_length=64),
) -> dict:
    try:
        meta = backup_adoption.adopt_s3_backup(
            key,
            source_ref=source_ref,
            expected_archive_sha256=expected_archive_sha256,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "backup_id": meta.id,
        "created_at": meta.created_at,
        "size_bytes": meta.size_bytes,
        "file_count": meta.file_count,
        "storage_backend": meta.storage_backend,
        "app_version": meta.app_version,
        "location": meta.location,
        "archive_sha256": meta.archive_sha256,
        "source_ref": meta.source_ref,
        "provider_ref": meta.provider_ref,
        "namespace": meta.namespace,
    }


@router.post(
    "/adopt-remote",
    dependencies=[Depends(require_superuser)],
    summary="Adopt one existing OpenDAL backup",
)
def adopt_remote_backup(
    connection_id: int = Query(..., ge=1),
    key: str = Query(..., min_length=1),
    source_ref: str = Query(..., min_length=1),
    expected_archive_sha256: str = Query(..., min_length=64, max_length=64),
) -> dict:
    try:
        meta = backup_adoption.adopt_opendal_backup(
            connection_id,
            key,
            source_ref=source_ref,
            expected_archive_sha256=expected_archive_sha256,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "backup_id": meta.id,
        "created_at": meta.created_at,
        "size_bytes": meta.size_bytes,
        "file_count": meta.file_count,
        "storage_backend": meta.storage_backend,
        "app_version": meta.app_version,
        "location": meta.location,
        "archive_sha256": meta.archive_sha256,
        "source_ref": meta.source_ref,
        "provider_ref": meta.provider_ref,
        "namespace": meta.namespace,
    }


@router.get(
    "/capabilities/database",
    dependencies=[Depends(require_superuser)],
    summary="Get database backup capabilities",
)
def get_database_backup_capabilities() -> dict[str, str | bool]:
    capability = backup_snapshot.database_backup_capability()
    return {
        "database_backend": capability.database_backend,
        "create_supported": capability.create_supported,
        "restore_supported": capability.restore_supported,
    }


@router.get(
    "/{backup_id}",
    dependencies=[Depends(require_superuser)],
    summary="Get backup metadata",
)
def get_backup(backup_id: str, source_ref: str | None = None) -> dict:
    try:
        meta = backup_catalogue.get_backup(backup_id, source_ref=source_ref)
    except BackupIdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if meta is None:
        raise HTTPException(status_code=404, detail="backup_not_found")
    return {
        "backup_id": meta.id,
        "created_at": meta.created_at,
        "size_bytes": meta.size_bytes,
        "file_count": meta.file_count,
        "storage_backend": meta.storage_backend,
        "app_version": meta.app_version,
        "location": meta.location,
        "archive_sha256": meta.archive_sha256,
        "source_ref": meta.source_ref,
        "provider_ref": meta.provider_ref,
        "namespace": meta.namespace,
        "operations": backup_operations(meta),
    }


@router.post(
    "/{backup_id}/verify",
    dependencies=[Depends(require_superuser)],
    summary="Verify a backup archive",
)
def verify_backup(backup_id: str, source_ref: str | None = None) -> dict:
    try:
        result = (
            backup_verification.verify_backup(backup_id)
            if source_ref is None
            else backup_verification.verify_backup(backup_id, source_ref=source_ref)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except BackupIdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "backup_id": result.backup_id,
        "valid": result.valid,
        "app_compatible": result.app_compatible,
        "manifest_version": result.manifest_version,
        "checked_members": result.checked_members,
        "findings": result.findings,
    }


@router.get(
    "/{backup_id}/download",
    dependencies=[Depends(require_superuser)],
    summary="Download a backup archive",
)
def download_backup(
    background_tasks: BackgroundTasks,
    backup_id: str,
    source_ref: str | None = None,
) -> FileResponse:
    try:
        # Preserve the operator-facing archive name before resolving a
        # cloud-only source to its hashed, per-source cache path. Identity
        # conflicts can be raised by either lookup and must map to the same 409.
        meta = backup_catalogue.get_backup(
            backup_id,
            **({"source_ref": source_ref} if source_ref is not None else {}),
        )
        if meta is None:
            raise FileNotFoundError(backup_id)
        archive_filename = Path(meta.path).name
        archive_path = (
            backup_catalogue.get_backup_archive_path(backup_id)
            if source_ref is None
            else backup_catalogue.get_backup_archive_path(
                backup_id, source_ref=source_ref
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except BackupIdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("backup %s download failed", backup_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    background_tasks.add_task(backup_caches.cleanup_backup_cache, archive_path)
    return FileResponse(
        archive_path,
        media_type="application/gzip",
        filename=archive_filename,
    )


@router.delete(
    "/{backup_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_superuser)],
    summary="Delete a backup",
)
def delete_backup(backup_id: str, source_ref: str | None = None) -> dict:
    try:
        deleted = (
            backup_deletion.delete_backup(backup_id)
            if source_ref is None
            else backup_deletion.delete_backup(
                backup_id,
                source_ref=source_ref,
            )
        )
    except backup_contracts.BackupDeleteUnsupportedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except backup_contracts.BackupOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="backup_storage_ownership_unverified",
        ) from exc
    except BackupIdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="backup_not_found")
    return {"backup_id": backup_id, "deleted": True}


@router.post(
    "/{backup_id}/restore",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_superuser)],
    summary="Restore from a backup",
    description=(
        "Restores the database and all files from a backup archive. "
        "This is destructive — it replaces the current database and all "
        "files. It is strongly recommended to create a fresh backup first."
    ),
)
def restore_backup(backup_id: str, source_ref: str | None = None, session: Session = Depends(get_session)) -> dict:
    # Authorization has completed. Release its read transaction before the
    # restore coordinator locks/replaces PostgreSQL tables; keeping that same
    # request's users-table lock until response teardown would deadlock restore.
    session.rollback()
    try:
        result = (
            backup_restore.restore_backup(backup_id)
            if source_ref is None
            else backup_restore.restore_backup(backup_id, source_ref=source_ref)
        )
    except backup_contracts.DatabaseBackupNotSupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except backup_maintenance.RestoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except backup_contracts.BackupOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="backup_storage_ownership_unverified",
        ) from exc
    except BackupIdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OperationError:
        raise
    except Exception as exc:
        logger.exception("restore %s failed", backup_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result
