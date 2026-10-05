"""Exact replica retry reads; no archive rebuild or restore-cache fallback."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import app.modules.backups.backup.contracts as backup_contracts
import app.modules.backups.backup.publications as backup_publications
import app.modules.backups.backup.targets as backup_targets
import app.modules.backups.backup.verification as backup_verification
from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    BackupDestinationResult,
    BackupRun,
    OwnedStorageObject,
    StorageConnection,
    StorageObjectState,
)
from app.db.session import get_session_factory
from app.modules.backups.backup_destination import (
    BackupDestinationError,
    RemoteBackupDestination,
    destination_from_connection,
)
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_identity import StorageTargetIdentity
from app.modules.storage.storage_ownership import provider_ref_for_backend


class RetryRefused(RuntimeError):
    pass


if TYPE_CHECKING:
    from app.modules.backups.backup.targets import _BackupS3Target


@dataclass(frozen=True)
class LocalBinding:
    target: StorageTargetIdentity | None
    destination: LocalStorageBackend
    kind: Literal["local"] = "local"


@dataclass(frozen=True)
class S3Binding:
    target: StorageTargetIdentity | None
    destination: _BackupS3Target
    kind: Literal["s3"] = "s3"


@dataclass(frozen=True)
class RemoteBinding:
    target: StorageTargetIdentity | None
    destination: RemoteBackupDestination
    kind: Literal["connection"] = "connection"


Binding = LocalBinding | S3Binding | RemoteBinding


def binding_for(result: BackupDestinationResult) -> Binding:
    """Resolve current credentials, then require the saved exact target/locator."""

    if (
        not result.target_identity_json
        or not result.key
        or not result.namespace
        or not result.provider_ref
    ):
        raise RetryRefused("backup_retry_target_unverified")
    expected = StorageTargetIdentity.model_validate_json(result.target_identity_json)
    if expected.transport == "gdrive" and not expected.account:
        raise RetryRefused("backup_retry_target_unverified")
    if result.kind == "local":
        backend = LocalStorageBackend()
        configuration = json.loads(result.configuration_json)
        if str(Path(settings.backup_dir).resolve()) != configuration["directory"]:
            raise RetryRefused("backup_retry_target_changed")
        target = backend.storage_target
        namespace = backend.namespace_for(result.key)
        provider_ref = provider_ref_for_backend(backend, namespace=namespace)
        binding: Binding = LocalBinding(target, backend)
    elif result.kind == "s3":
        destination = backup_targets._get_backup_s3_target()
        if destination is None:
            raise RetryRefused("backup_retry_target_unavailable")
        target = destination.storage_target
        namespace = f"{destination.bucket}/{backup_contracts._BACKUP_S3_PREFIX}"
        provider_ref = destination.provider_ref
        binding = S3Binding(target, destination)
    else:
        with get_session_factory().scoped_session() as session:
            profile = session.get(StorageConnection, result.connection_id)
            if profile is None:
                raise RetryRefused("backup_retry_target_unavailable")
            try:
                destination = destination_from_connection(profile)
            except BackupDestinationError as exc:
                raise RetryRefused("backup_retry_target_unavailable") from exc
        target = destination.backend.storage_target
        namespace, provider_ref = destination.namespace, destination.provider_ref
        binding = RemoteBinding(target, destination)
    if (
        target != expected
        or namespace != result.namespace
        or provider_ref != result.provider_ref
    ):
        raise RetryRefused("backup_retry_target_changed")
    return binding


def owned_for(result: BackupDestinationResult, run: BackupRun) -> OwnedStorageObject:
    with get_session_factory().scoped_session() as session:
        row = session.get(OwnedStorageObject, result.ownership_id)
        if (
            row is None
            or row.state != StorageObjectState.COMMITTED
            or row.object_kind != "backup"
            or row.key != result.key
            or row.namespace != result.namespace
            or row.provider_ref != result.provider_ref
            or row.sha256 != run.archive_sha256
            or row.size_bytes != run.size_bytes
        ):
            raise RetryRefused("backup_retry_source_unverified")
        return OwnedStorageObject.model_validate(row.model_dump())


def _copy_exact(reader, destination: Path, run: BackupRun) -> None:
    digest = hashlib.sha256()
    written = 0
    with destination.open("xb") as output:
        while chunk := reader.read(
            min(1024 * 1024, (run.size_bytes or 0) - written + 1)
        ):
            written += len(chunk)
            if written > (run.size_bytes or 0):
                raise RetryRefused("backup_retry_source_changed")
            digest.update(chunk)
            output.write(chunk)
    if written != run.size_bytes or digest.hexdigest() != run.archive_sha256:
        raise RetryRefused("backup_retry_source_changed")


@contextmanager
def verified_survivor(result: BackupDestinationResult, run: BackupRun):
    """Yield private verified bytes only after reading their live owned source."""
    from app.modules.backups import backup_runs

    binding = binding_for(result)
    row = owned_for(result, run)
    with tempfile.TemporaryDirectory(prefix=".printstash-replica-retry-") as directory:
        path = Path(directory) / run.archive_name
        if binding.kind == "local":
            fd = os.open(row.key, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as reader:
                before = os.fstat(reader.fileno())
                if (before.st_dev, before.st_ino, before.st_size) != (
                    row.device,
                    row.inode,
                    row.size_bytes,
                ):
                    raise RetryRefused("backup_retry_source_changed")
                _copy_exact(reader, path, run)
                after = os.fstat(reader.fileno())
                current = os.stat(row.key, follow_symlinks=False)

                def identity(value):
                    return (
                        value.st_dev,
                        value.st_ino,
                        value.st_size,
                        value.st_mtime_ns,
                        value.st_ctime_ns,
                    )

                if identity(before) != identity(after) or identity(after) != identity(
                    current
                ):
                    raise RetryRefused("backup_retry_source_changed")
        elif binding.kind == "s3":
            before = backup_targets._s3_head_owned(binding.destination, row)
            response = backup_targets._s3_get_owned(binding.destination, row)
            body = response["Body"]
            try:
                _copy_exact(body, path, run)
            finally:
                body.close()
            after = backup_targets._s3_head_owned(binding.destination, row)
            if any(
                before.get(key) != after.get(key)
                for key in ("ContentLength", "ETag", "VersionId")
            ):
                raise RetryRefused("backup_retry_source_changed")
        else:
            binding.destination.download_owned(row, path)
        checked = backup_verification.verify_backup(
            run.backup_id, archive_path=path, record_audit=False
        )
        if not checked.valid or not checked.app_compatible:
            raise RetryRefused("backup_retry_source_unverified")
        # Credentials or target configuration may have changed during the read.
        binding_for(result)
        backup_runs.update_result(result.id, verified_at=utcnow())
        yield path


def publish_retry(result: BackupDestinationResult, run: BackupRun, path: Path) -> None:
    """Reuse only this locator's reservation and its create-only publication."""
    import uuid
    from dataclasses import replace

    from sqlmodel import select

    from app.modules.backups import backup_runs
    from app.modules.storage.storage_backend.contracts import CreationReceipt
    from app.modules.storage.storage_ownership import (
        complete_publication,
        fail_publication,
        reserve_publication,
    )
    from app.modules.storage.storage_publication import PublicationReservation

    if (
        result.key is None
        or result.namespace is None
        or result.provider_ref is None
        or run.size_bytes is None
    ):
        raise RetryRefused("backup_retry_target_unverified")
    binding = binding_for(result)
    with get_session_factory().scoped_session() as session:
        existing = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.key == result.key,
                OwnedStorageObject.namespace == result.namespace,
                OwnedStorageObject.provider_ref == result.provider_ref,
                OwnedStorageObject.object_kind == "backup",
                OwnedStorageObject.state != StorageObjectState.RETIRING,
            )
        ).one_or_none()
        if existing is not None and (
            existing.sha256 != run.archive_sha256
            or existing.size_bytes != run.size_bytes
        ):
            raise RetryRefused("backup_retry_publication_conflict")
        previous = PublicationReservation.of(existing) if existing is not None else None
    # Absence is required before attempting another create. A concurrent writer
    # after this observation is still protected by create-only publication.
    if binding.kind == "s3":
        try:
            binding.destination.client.head_object(
                Bucket=binding.destination.bucket, Key=result.key
            )
        except Exception as exc:
            response = getattr(exc, "response", {})
            if str(response.get("Error", {}).get("Code")) not in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                raise RetryRefused("backup_retry_target_unavailable") from exc
        else:
            raise RetryRefused("backup_retry_publication_conflict")
    elif binding.kind == "local":
        if os.path.lexists(result.key):
            raise RetryRefused("backup_retry_publication_conflict")
    elif binding.destination.backend.object_info(result.key) is not None:
        raise RetryRefused("backup_retry_publication_conflict")

    token = uuid.uuid4().hex
    with get_session_factory().scoped_session() as session:
        backend_name = (
            "backup-s3"
            if binding.kind == "s3"
            else binding.destination.backend_name
            if binding.kind == "local"
            else binding.destination.backend.backend_name
        )
        reservation_id = reserve_publication(
            session,
            backend=backend_name,
            namespace=result.namespace,
            key=result.key,
            provider_ref=result.provider_ref,
            object_kind="backup",
            expected_size=run.size_bytes,
            sha256=run.archive_sha256,
            token=token if binding.kind == "s3" else None,
            previous=previous,
        )
        session.commit()
    try:
        binding_for(result)
        with path.open("rb") as source:
            if binding.kind == "local":
                receipt = binding.destination.create_stream(source, result.key)
            elif binding.kind == "s3":
                target = binding.destination
                target.client.put_object(
                    Bucket=target.bucket,
                    Key=result.key,
                    Body=source,
                    IfNoneMatch="*",
                    Metadata={"printstash-create-token": token},
                )
                info = target.client.head_object(Bucket=target.bucket, Key=result.key)
                backup_targets._require_remote_identity(info)
                if (
                    info.get("Metadata", {}).get("printstash-create-token") != token
                    or info.get("ContentLength") != run.size_bytes
                ):
                    raise RetryRefused("backup_retry_publication_conflict")
                receipt = CreationReceipt(
                    key=result.key,
                    size=run.size_bytes,
                    token=token,
                    backend="backup-s3",
                    namespace=result.namespace,
                    provider_ref=result.provider_ref,
                    etag=info.get("ETag"),
                    version_id=info.get("VersionId"),
                )
            else:
                receipt = binding.destination.backend.publish_replica(
                    source, result.key
                )
        with get_session_factory().scoped_session() as session:
            complete_publication(
                session,
                reservation_id,
                replace(receipt, provider_ref=result.provider_ref),
                object_kind="backup",
                sha256=run.archive_sha256,
                provider_ref=result.provider_ref,
            )
            session.commit()
    except Exception as exc:
        with get_session_factory().scoped_session() as session:
            fail_publication(session, reservation_id, exc)
            session.commit()
        raise
    meta = backup_contracts.BackupMeta(
        id=run.backup_id,
        created_at=run.created_at.isoformat(),
        size_bytes=run.size_bytes,
        storage_backend=run.storage_backend,
        file_count=run.file_count or 0,
        app_version=run.app_version,
        path=result.key,
        location="local"
        if binding.kind == "local"
        else "s3"
        if binding.kind == "s3"
        else binding.destination.location,
        archive_sha256=run.archive_sha256,
        provider_ref=result.provider_ref,
        namespace=result.namespace,
    )
    meta.source_ref = backup_targets.source_reference(
        location=meta.location,
        namespace=meta.namespace,
        path=meta.path,
        provider_ref=meta.provider_ref,
    )
    backup_runs.publication_completed(result.id, meta)


def reconcile_result(result: BackupDestinationResult, run: BackupRun) -> bool:
    from sqlmodel import select

    from app.modules.backups import backup_runs

    if not result.key or not result.provider_ref or not run.archive_sha256:
        return False
    binding_for(result)
    with get_session_factory().scoped_session() as session:
        row = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.key == result.key,
                OwnedStorageObject.namespace == result.namespace,
                OwnedStorageObject.provider_ref == result.provider_ref,
                OwnedStorageObject.object_kind == "backup",
                OwnedStorageObject.sha256 == run.archive_sha256,
                OwnedStorageObject.state != StorageObjectState.RETIRING,
            )
        ).one_or_none()
        if row is None:
            return False
        ownership_id = row.id
    backup_publications.reconcile_backup_publications(ownership_id=ownership_id)
    with get_session_factory().scoped_session() as session:
        row = session.get(OwnedStorageObject, ownership_id)
        if row is None or row.state != StorageObjectState.COMMITTED:
            return False
    candidate = BackupDestinationResult.model_validate(result.model_dump())
    candidate.ownership_id = ownership_id
    with verified_survivor(candidate, run):
        pass
    binding = binding_for(result)
    location = (
        "local"
        if binding.kind == "local"
        else "s3"
        if binding.kind == "s3"
        else binding.destination.location
    )
    source_ref = backup_targets.source_reference(
        location=location,
        namespace=result.namespace,
        path=result.key,
        provider_ref=result.provider_ref,
    )
    backup_runs.update_result(
        result.id,
        ownership_id=ownership_id,
        source_ref=source_ref,
        outcome="completed",
        error_code=None,
        published_at=result.published_at or utcnow(),
    )
    return True
