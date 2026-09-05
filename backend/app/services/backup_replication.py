"""Replica publication orchestration; restore remains owned by backup.py."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import OwnedStorageObject, StorageObjectState
from app.db.session import get_session_factory
from app.services import backup_runs
from app.services.backup_destination import destination_from_connection
from app.services.storage_backend import CreationReceipt, LocalStorageBackend
from app.services.storage_ownership import (
    provider_ref_for_backend,
    publish_file,
    record_creation,
)

logger = get_logger(__name__)


def prepare_destinations(selected):
    from app.services import backup

    target = None
    if selected.s3_result_id is not None:
        try:
            if backup._stable_backup_s3_config() != selected.s3_configuration:
                raise RuntimeError("backup_target_changed")
            target = backup._get_backup_s3_target()
            if target is None or target.signature != backup._backup_s3_signature(
                selected.s3_configuration
            ):
                raise RuntimeError("backup_target_unavailable")
        except Exception:
            target = None
            backup_runs.update_result(
                selected.s3_result_id,
                outcome="failed",
                error_code="backup_target_unavailable",
            )
    destinations = []
    for result_id, connection in selected.connections:
        try:
            destinations.append((result_id, destination_from_connection(connection)))
        except Exception:
            backup_runs.update_result(
                result_id, outcome="failed", error_code="storage_connection_invalid"
            )
    return target, destinations


def publish_archive(
    selected,
    *,
    archive_temp: Path,
    archive_path: Path,
    archive_name: str,
    backup_id: str,
    ts: str,
    backend_name: str,
    file_count: int,
    written_files: int,
    final_size: int,
    archive_sha256: str,
    target,
    remote_destinations,
):
    from app.services.backup import (
        _BACKUP_S3_PREFIX,
        BackupMeta,
        _backup_s3_key,
        _require_remote_identity,
        _source_ref,
    )

    keep_local = selected.local_result_id is not None
    created_sources: list[BackupMeta] = []

    if keep_local:
        try:
            local_backend = LocalStorageBackend()
            local_namespace = local_backend.namespace_for(str(archive_path))
            local_provider_ref = provider_ref_for_backend(
                local_backend, namespace=local_namespace
            )
            backup_runs.publication_started(
                selected.local_result_id,
                key=str(archive_path),
                namespace=local_namespace,
                provider_ref=local_provider_ref,
                target=local_backend.storage_target,
            )
            with get_session_factory().session() as publish_session:
                local_receipt = publish_file(
                    publish_session,
                    local_backend,
                    str(archive_path),
                    archive_temp,
                    object_kind="backup",
                )
                publish_session.commit()
            created_sources.append(
                BackupMeta(
                    id=backup_id,
                    created_at=ts,
                    size_bytes=local_receipt.size,
                    storage_backend=backend_name,
                    file_count=file_count,
                    app_version=settings.app_version,
                    path=str(archive_path),
                    location="local",
                    archive_sha256=archive_sha256,
                    provider_ref=local_provider_ref,
                    namespace=local_namespace,
                    source_ref=_source_ref(
                        location="local",
                        namespace=local_namespace,
                        path=str(archive_path),
                        provider_ref=local_provider_ref,
                    ),
                )
            )
            backup_runs.publication_completed(
                selected.local_result_id, created_sources[-1]
            )
            logger.info(
                "backup %s created locally: %d files, %.1f MiB",
                backup_id,
                written_files,
                final_size / (1024 * 1024),
            )
        except Exception:
            backup_runs.update_result(
                selected.local_result_id,
                outcome="failed",
                error_code="backup_local_publication_failed",
            )
            logger.warning("backup %s: local publication failed", backup_id)

    # Upload to S3 if configured
    if target:
        s3 = target.client
        bucket = target.bucket
        try:
            s3_key = _backup_s3_key(archive_name)
            namespace = f"{bucket}/{_BACKUP_S3_PREFIX}"
            backup_runs.publication_started(
                selected.s3_result_id,
                key=s3_key,
                namespace=namespace,
                provider_ref=target.provider_ref,
                target=target.storage_target,
            )
            token = uuid.uuid4().hex
            with get_session_factory().session() as reservation_session:
                reservation = OwnedStorageObject(
                    backend="backup-s3",
                    namespace=namespace,
                    key=s3_key,
                    object_kind="backup",
                    provider_ref=target.provider_ref,
                    state=StorageObjectState.PENDING,
                    size_bytes=final_size,
                    sha256=archive_sha256,
                    token=token,
                )
                reservation_session.add(reservation)
                reservation_session.commit()
            with archive_temp.open("rb") as source:
                s3.put_object(
                    Bucket=bucket,
                    Key=s3_key,
                    Body=source,
                    IfNoneMatch="*",
                    Metadata={"printstash-create-token": token},
                )
            # A PUT response is not a portable proof (notably, many S3
            # compatible services omit VersionId or return a non-content
            # ETag). Capture the object identity from HEAD before committing
            # the ledger row, and ensure the create token/size still match.
            response = s3.head_object(Bucket=bucket, Key=s3_key)
            _require_remote_identity(response)
            if (
                int(response.get("ContentLength", -1)) != final_size
                or response.get("Metadata", {}).get("printstash-create-token") != token
            ):
                raise RuntimeError("backup_publication_evidence_mismatch")
            s3_receipt = CreationReceipt(
                key=s3_key,
                size=final_size,
                token=token,
                backend="backup-s3",
                namespace=namespace,
                etag=str(response.get("ETag")) if response.get("ETag") else None,
                version_id=(
                    str(response.get("VersionId"))
                    if response.get("VersionId")
                    else None
                ),
            )
            with get_session_factory().session() as commit_session:
                record_creation(
                    commit_session,
                    s3_receipt,
                    object_kind="backup",
                    provider_ref=target.provider_ref,
                )
                commit_session.commit()
            created_sources.append(
                BackupMeta(
                    id=backup_id,
                    created_at=ts,
                    size_bytes=final_size,
                    storage_backend=backend_name,
                    file_count=file_count,
                    app_version=settings.app_version,
                    path=s3_key,
                    location="s3",
                    archive_sha256=archive_sha256,
                    provider_ref=target.provider_ref,
                    namespace=namespace,
                    source_ref=_source_ref(
                        location="s3",
                        namespace=namespace,
                        path=s3_key,
                        provider_ref=target.provider_ref,
                    ),
                )
            )
            backup_runs.publication_completed(
                selected.s3_result_id, created_sources[-1]
            )
            logger.info("backup %s uploaded to S3: %s", backup_id, s3_key)
        except Exception:
            backup_runs.update_result(
                selected.s3_result_id,
                outcome="failed",
                error_code="backup_s3_publication_failed",
            )
            logger.warning("backup %s: S3 upload failed", backup_id)

    # Purpose-scoped connections are separate replicas. A failed remote
    # destination never invalidates the already committed local archive, and a
    # failure at one provider does not prevent the remaining replicas.
    for result_id, destination in remote_destinations:
        try:
            remote_key = destination.key(archive_name)
            backup_runs.publication_started(
                result_id,
                key=remote_key,
                namespace=destination.namespace,
                provider_ref=destination.provider_ref,
                target=destination.backend.storage_target,
            )
            with get_session_factory().session() as remote_session:
                remote_receipt = destination.publish_file(
                    remote_session, remote_key, archive_temp, sha256=archive_sha256
                )
                remote_session.commit()
            created_sources.append(
                BackupMeta(
                    id=backup_id,
                    created_at=ts,
                    size_bytes=remote_receipt.size,
                    storage_backend=backend_name,
                    file_count=file_count,
                    app_version=settings.app_version,
                    path=remote_key,
                    location=destination.location,
                    archive_sha256=archive_sha256,
                    provider_ref=destination.provider_ref,
                    namespace=destination.namespace,
                    source_ref=_source_ref(
                        location=destination.location,
                        namespace=destination.namespace,
                        path=remote_key,
                        provider_ref=destination.provider_ref,
                    ),
                )
            )
            backup_runs.publication_completed(result_id, created_sources[-1])
            logger.info(
                "backup %s replicated through OpenDAL provider %s",
                backup_id,
                destination.provider,
            )
        except Exception:
            backup_runs.update_result(
                result_id,
                outcome="failed",
                error_code="backup_remote_publication_failed",
            )
            logger.warning(
                "backup %s: OpenDAL replica %s failed",
                backup_id,
                destination.name,
            )

    return created_sources
