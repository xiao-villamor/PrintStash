"""Backup & restore endpoints."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.core.logging import get_logger
from app.core.security import require_superuser
from app.services import backup

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
        meta = backup.create_backup()
    except backup.DatabaseBackupNotSupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    background_tasks.add_task(backup.purge_old_backups)
    return {
        "backup_id": meta.id,
        "created_at": meta.created_at,
        "size_bytes": meta.size_bytes,
        "file_count": meta.file_count,
        "storage_backend": meta.storage_backend,
        "app_version": meta.app_version,
        "location": meta.location,
    }


@router.get(
    "",
    dependencies=[Depends(require_superuser)],
    summary="List available backups",
    description="Returns backups from local storage and (if configured) cloud storage, merged and deduplicated.",
)
def list_backups() -> list[dict]:
    metas = backup.list_backups()
    return [
        {
            "backup_id": m.id,
            "created_at": m.created_at,
            "size_bytes": m.size_bytes,
            "file_count": m.file_count,
            "storage_backend": m.storage_backend,
            "app_version": m.app_version,
            "location": m.location,
        }
        for m in metas
    ]


@router.get(
    "/capabilities/database",
    dependencies=[Depends(require_superuser)],
    summary="Get database backup capabilities",
)
def get_database_backup_capabilities() -> dict[str, str | bool]:
    capability = backup.database_backup_capability()
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
def get_backup(backup_id: str) -> dict:
    meta = backup.get_backup(backup_id)
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
    }


@router.post(
    "/{backup_id}/verify",
    dependencies=[Depends(require_superuser)],
    summary="Verify a backup archive",
)
def verify_backup(backup_id: str) -> dict:
    try:
        result = backup.verify_backup(backup_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
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
def download_backup(backup_id: str) -> FileResponse:
    try:
        archive_path = backup.get_backup_archive_path(backup_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except Exception as exc:
        logger.exception("backup %s download failed", backup_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(
        archive_path,
        media_type="application/gzip",
        filename=archive_path.name,
    )


@router.delete(
    "/{backup_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_superuser)],
    summary="Delete a backup",
)
def delete_backup(backup_id: str) -> dict:
    if not backup.delete_backup(backup_id):
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
def restore_backup(backup_id: str) -> dict:
    try:
        result = backup.restore_backup(backup_id)
    except backup.DatabaseBackupNotSupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except backup.RestoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("restore %s failed", backup_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result
