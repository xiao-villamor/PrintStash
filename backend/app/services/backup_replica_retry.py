"""Exact replica retry reads; no archive rebuild or restore-cache fallback."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

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
from app.services.backup_destination import destination_from_connection
from app.services.storage_backend import LocalStorageBackend
from app.services.storage_identity import StorageTargetIdentity
from app.services.storage_ownership import provider_ref_for_backend


class RetryRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class Binding:
    target: object
    destination: object
    kind: str


def binding_for(result: BackupDestinationResult) -> Binding:
    """Resolve current credentials, then require the saved exact target/locator."""
    from app.services import backup

    if (
        not result.target_identity_json
        or not result.key
        or not result.namespace
        or not result.provider_ref
    ):
        raise RetryRefused("backup_retry_target_unverified")
    expected = StorageTargetIdentity.model_validate_json(result.target_identity_json)
    if result.kind == "local":
        backend = LocalStorageBackend()
        configuration = json.loads(result.configuration_json)
        if str(Path(settings.backup_dir).resolve()) != configuration["directory"]:
            raise RetryRefused("backup_retry_target_changed")
        target = backend.storage_target
        namespace = backend.namespace_for(result.key)
        provider_ref = provider_ref_for_backend(backend, namespace=namespace)
        destination = backend
    elif result.kind == "s3":
        destination = backup._get_backup_s3_target()
        if destination is None:
            raise RetryRefused("backup_retry_target_unavailable")
        target = destination.storage_target
        namespace = f"{destination.bucket}/{backup._BACKUP_S3_PREFIX}"
        provider_ref = destination.provider_ref
    else:
        with get_session_factory().scoped_session() as session:
            profile = session.get(StorageConnection, result.connection_id)
            if profile is None:
                raise RetryRefused("backup_retry_target_unavailable")
            destination = destination_from_connection(profile)
        target = destination.backend.storage_target
        namespace, provider_ref = destination.namespace, destination.provider_ref
    if (
        target != expected
        or namespace != result.namespace
        or provider_ref != result.provider_ref
    ):
        raise RetryRefused("backup_retry_target_changed")
    return Binding(target, destination, result.kind)


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
    from app.services import backup, backup_runs

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
            before = backup._s3_head_owned(binding.destination, row)
            response = backup._s3_get_owned(binding.destination, row)
            body = response["Body"]
            try:
                _copy_exact(body, path, run)
            finally:
                body.close()
            after = backup._s3_head_owned(binding.destination, row)
            if any(
                before.get(key) != after.get(key)
                for key in ("ContentLength", "ETag", "VersionId")
            ):
                raise RetryRefused("backup_retry_source_changed")
        else:
            binding.destination.download_owned(row, path)
        checked = backup.verify_backup(
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

    from app.services import backup, backup_runs
    from app.services.storage_backend import CreationReceipt
    from app.services.storage_ownership import (
        complete_publication,
        fail_publication,
        reserve_creation,
    )

    binding = binding_for(result)
    with get_session_factory().scoped_session() as session:
        existing = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.key == result.key,
                OwnedStorageObject.namespace == result.namespace,
                OwnedStorageObject.provider_ref == result.provider_ref,
                OwnedStorageObject.object_kind == "backup",
            )
        ).one_or_none()
        if existing is not None and (
            existing.sha256 != run.archive_sha256
            or existing.size_bytes != run.size_bytes
        ):
            raise RetryRefused("backup_retry_publication_conflict")
        reservation_id = existing.id if existing is not None else None
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
        if reservation_id is not None:
            existing = session.get(OwnedStorageObject, reservation_id)
            existing.state = StorageObjectState.PENDING
            existing.last_error = None
            existing.token = token if binding.kind == "s3" else existing.token
            session.add(existing)
            session.commit()
        elif binding.kind == "s3":
            existing = OwnedStorageObject(
                backend="backup-s3",
                namespace=result.namespace,
                key=result.key,
                provider_ref=result.provider_ref,
                object_kind="backup",
                state=StorageObjectState.PENDING,
                token=token,
                sha256=run.archive_sha256,
                size_bytes=run.size_bytes,
            )
            session.add(existing)
            session.commit()
            reservation_id = existing.id
        else:
            backend = (
                binding.destination
                if binding.kind == "local"
                else binding.destination.backend
            )
            reservation_id = reserve_creation(
                session,
                backend,
                result.key,
                object_kind="backup",
                expected_size=run.size_bytes,
                sha256=run.archive_sha256,
                provider_ref=result.provider_ref,
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
                backup._require_remote_identity(info)
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
    meta = backup.BackupMeta(
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
    meta.source_ref = backup._source_ref(
        location=meta.location,
        namespace=meta.namespace,
        path=meta.path,
        provider_ref=meta.provider_ref,
    )
    backup_runs.publication_completed(result.id, meta)
