"""Replica publication orchestration; journaled restore lives in backup.restore."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import app.modules.backups.backup.targets as backup_targets
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.modules.backups import backup_runs
from app.modules.backups.backup.contracts import BackupProgress, BackupStage
from app.modules.backups.backup_destination import (
    RemoteBackupDestination,
    destination_from_connection,
)
from app.modules.storage.storage_backend.contracts import CreationReceipt
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_ownership import (
    complete_publication,
    provider_ref_for_backend,
    publish_file,
    reserve_publication,
)

logger = get_logger(__name__)


def publish_replica(
    destination: RemoteBackupDestination,
    session,
    key: str,
    source: Path,
    *,
    sha256: str,
) -> CreationReceipt:
    """Reserve and commit a replica without borrowing managed creation authority."""
    from app.db.publication import require_clean_publication_transaction
    from app.modules.storage.storage_ownership import (
        complete_publication,
        fail_publication,
        reserve_creation,
    )

    require_clean_publication_transaction(session)
    reservation_id = reserve_creation(
        session,
        destination.backend,
        key,
        object_kind="backup",
        expected_size=source.stat().st_size,
        sha256=sha256,
        provider_ref=destination.provider_ref,
    )
    try:
        with source.open("rb") as reader:
            receipt = destination.backend.publish_replica(reader, key)
    except Exception as exc:
        fail_publication(session, reservation_id, exc)
        raise
    receipt = replace(receipt, provider_ref=destination.provider_ref)
    complete_publication(
        session,
        reservation_id,
        receipt,
        object_kind="backup",
        sha256=sha256,
        provider_ref=destination.provider_ref,
    )
    return receipt


def prepare_destinations(selected):

    target = None
    if selected.s3_result_id is not None:
        try:
            if backup_targets._stable_backup_s3_config() != selected.s3_configuration:
                raise RuntimeError("backup_target_changed")
            target = backup_targets._get_backup_s3_target()
            if (
                target is None
                or target.signature
                != backup_targets._backup_s3_signature(selected.s3_configuration)
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
    progress: BackupProgress | None = None,
):
    from app.modules.backups.backup.contracts import _BACKUP_S3_PREFIX, BackupMeta
    from app.modules.backups.backup.targets import (
        _backup_s3_key,
        _require_remote_identity,
        source_reference,
    )

    keep_local = selected.local_result_id is not None
    created_sources: list[BackupMeta] = []

    if keep_local:
        if progress is not None:
            progress(BackupStage.PUBLISHING, destination="Local")
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
                    sha256=archive_sha256,
                    # The build temp sits beside the archive, so a move is a
                    # hard link. Replicas below still read the temp; keep it
                    # for them and copy only then.
                    move=not target and not remote_destinations,
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
                    source_ref=source_reference(
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
        if progress is not None:
            progress(BackupStage.PUBLISHING, destination="S3")
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
                reservation = reserve_publication(
                    reservation_session,
                    backend="backup-s3",
                    namespace=namespace,
                    key=s3_key,
                    object_kind="backup",
                    provider_ref=target.provider_ref,
                    expected_size=final_size,
                    sha256=archive_sha256,
                    token=token,
                )
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
                complete_publication(
                    commit_session,
                    reservation,
                    s3_receipt,
                    object_kind="backup",
                    sha256=archive_sha256,
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
                    source_ref=source_reference(
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
        if progress is not None:
            progress(BackupStage.PUBLISHING, destination=destination.name)
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
                remote_receipt = publish_replica(
                    destination,
                    remote_session,
                    remote_key,
                    archive_temp,
                    sha256=archive_sha256,
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
                    source_ref=source_reference(
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
