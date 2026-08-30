"""Backup & restore endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
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
            "archive_sha256": m.archive_sha256,
            "source_ref": m.source_ref,
            "provider_ref": m.provider_ref,
            "namespace": m.namespace,
            "key": m.path,
            "prefix": backup._s3_prefix_for_key(m.path),  # noqa: SLF001
            "canonical": m.canonical,
            "precedence": m.precedence,
        }
        for m in metas
    ]


@router.get(
    "/sources",
    dependencies=[Depends(require_superuser)],
    summary="List every exact backup source",
)
def list_backup_sources() -> list[dict]:
    """Expose replicas and collision candidates without collapsing locators."""
    metas = backup.list_backup_sources()
    return [
        {
            "backup_id": m.id,
            "created_at": m.created_at,
            "size_bytes": m.size_bytes,
            "file_count": m.file_count,
            "storage_backend": m.storage_backend,
            "app_version": m.app_version,
            "location": m.location,
            "archive_sha256": m.archive_sha256,
            "source_ref": m.source_ref,
            "provider_ref": m.provider_ref,
            "namespace": m.namespace,
            "key": m.path,
            "prefix": backup._s3_prefix_for_key(m.path),  # noqa: SLF001
            "canonical": m.canonical,
            "precedence": m.precedence,
        }
        for m in metas
    ]


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
        meta = backup.adopt_local_backup(filename)
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
    }


@router.get(
    "/unowned-local",
    dependencies=[Depends(require_superuser)],
    summary="Discover valid unowned legacy local backups",
)
def discover_unowned_local_backups() -> list[dict[str, object]]:
    return backup.discover_unowned_local_backups()


@router.get(
    "/unowned-s3",
    dependencies=[Depends(require_superuser)],
    summary="Discover valid unowned legacy S3 backups",
)
def discover_unowned_s3_backups() -> list[dict[str, object]]:
    return backup.discover_unowned_s3_backups()


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
        meta = backup.adopt_s3_backup(
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
def get_backup(backup_id: str, source_ref: str | None = None) -> dict:
    try:
        meta = backup.get_backup(backup_id, source_ref=source_ref)
    except backup.BackupIdentityConflictError as exc:
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
    }


@router.post(
    "/{backup_id}/verify",
    dependencies=[Depends(require_superuser)],
    summary="Verify a backup archive",
)
def verify_backup(backup_id: str, source_ref: str | None = None) -> dict:
    try:
        result = (
            backup.verify_backup(backup_id)
            if source_ref is None
            else backup.verify_backup(backup_id, source_ref=source_ref)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except backup.BackupIdentityConflictError as exc:
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
        meta = backup.get_backup(
            backup_id,
            **({"source_ref": source_ref} if source_ref is not None else {}),
        )
        if meta is None:
            raise FileNotFoundError(backup_id)
        archive_filename = Path(meta.path).name
        archive_path = (
            backup.get_backup_archive_path(backup_id)
            if source_ref is None
            else backup.get_backup_archive_path(backup_id, source_ref=source_ref)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except backup.BackupIdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("backup %s download failed", backup_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    background_tasks.add_task(backup.cleanup_backup_cache, archive_path)
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
            backup.delete_backup(backup_id)
            if source_ref is None
            else backup.delete_backup(backup_id, source_ref=source_ref)
        )
    except backup.BackupOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="backup_storage_ownership_unverified",
        ) from exc
    except backup.BackupIdentityConflictError as exc:
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
def restore_backup(backup_id: str, source_ref: str | None = None) -> dict:
    try:
        result = (
            backup.restore_backup(backup_id)
            if source_ref is None
            else backup.restore_backup(backup_id, source_ref=source_ref)
        )
    except backup.DatabaseBackupNotSupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="backup_not_found") from exc
    except backup.RestoreConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except backup.BackupOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="backup_storage_ownership_unverified",
        ) from exc
    except backup.BackupIdentityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("restore %s failed", backup_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result
