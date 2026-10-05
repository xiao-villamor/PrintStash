"""Explicit adoption of existing archives into the ownership catalogue."""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO

from sqlmodel import select

import app.modules.backups.backup.archive_format as _archive_format_module
import app.modules.backups.backup.catalogue as _catalogue_module
import app.modules.backups.backup.contracts as _contracts_module
import app.modules.backups.backup.snapshot as _snapshot_module
import app.modules.backups.backup.targets as _targets_module
import app.modules.backups.backup.verification as _verification_module
import app.modules.backups.backup_destination as _backup_destination_module
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    OwnedStorageObject,
    StorageObjectState,
)
from app.db.session import get_session_factory
from app.modules.administration import audit
from app.modules.storage import storage
from app.modules.storage.capacity import CapacityManager, CapacityResource
from app.modules.storage.storage_backend.contracts import CreationReceipt
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_ownership import (
    adopt_publication,
    prepare_file,
    provider_ref_for_backend,
    record_creation,
)
from app.runtime.maintenance import exclusive_backup_operation

logger = get_logger(__name__)


def _validate_archive_for_adoption(archive_path: Path) -> _contracts_module.BackupMeta:
    """Validate an unowned local archive before making it listable.

    Legacy archives are intentionally not auto-adopted during listing: an
    administrator must identify the exact file.  The same manifest, storage
    namespace, member, hash, and SQLite checks used by restore run before a
    durable ownership row is created.
    """
    meta = _archive_format_module._read_manifest(archive_path)
    if meta is None:
        raise RuntimeError("backup_manifest_invalid")
    # Use the same complete validator as operator verification, including
    # exact manifest membership, member sizes/digests, safe paths, and the
    # archived SQLite integrity check.  Discovery and adoption must never
    # commit a merely parseable tarball.
    verification = _verification_module.verify_backup(
        meta.id, archive_path=archive_path, record_audit=False
    )
    if not verification.valid:
        raise RuntimeError(
            str(verification.findings[0].get("code", "backup_manifest_invalid"))
        )
    with tarfile.open(archive_path, mode="r:gz") as tar:
        members = tar.getmembers()
        if any(
            _archive_format_module._unsafe_member_name(member.name)
            or member.issym()
            or member.islnk()
            for member in members
        ):
            raise RuntimeError("backup_manifest_invalid")
        manifest, entries = _archive_format_module._restore_manifest_entries(tar)
        declared = set(entries)
        regular = {member.name for member in members if member.isfile()}
        if regular != {"manifest.json", "db.sqlite3", *declared}:
            raise RuntimeError("backup_manifest_invalid")
        if manifest.get("file_count") is not None and manifest.get("file_count") != len(
            entries
        ):
            raise RuntimeError("backup_manifest_invalid")
        db_members = [member for member in members if member.name == "db.sqlite3"]
        if len(db_members) != 1 or not db_members[0].isfile():
            raise RuntimeError("backup_member_missing:db.sqlite3")
        # Validate the archived DB without trusting the current database.
        fd, raw_db = tempfile.mkstemp(prefix=".printstash-adopt-db-")
        os.close(fd)
        db_path = Path(raw_db)
        try:
            stream = tar.extractfile(db_members[0])
            if stream is None:
                raise RuntimeError("backup_member_missing:db.sqlite3")
            with db_path.open("wb") as destination:
                shutil.copyfileobj(stream, destination)
            _snapshot_module._validate_sqlite_snapshot(db_path)
        finally:
            db_path.unlink(missing_ok=True)
    return meta


def _download_s3_archive(
    target: _targets_module._BackupS3Target,
    key: str,
    row: OwnedStorageObject | None = None,
    *,
    version_id: str | None = None,
    etag: str | None = None,
) -> tuple[Path, dict]:
    """Download one exact remote object into a private temporary file."""
    if row is not None:
        response = _targets_module._s3_get_owned(target, row)
    else:
        kwargs: dict[str, str] = {"Bucket": target.bucket, "Key": key}
        if version_id:
            kwargs["VersionId"] = version_id
        elif etag:
            kwargs["IfMatch"] = etag
        else:
            raise _contracts_module.BackupOwnershipError(
                "backup_remote_identity_unavailable"
            )
        response = target.client.get_object(**kwargs)
    body = response["Body"]
    try:
        estimate = int(response.get("ContentLength") or settings.max_upload_bytes)
        with CapacityManager(get_session_factory()).hold(
            f"backup-adoption:{secrets.token_hex(12)}",
            [
                CapacityResource.for_path(
                    settings.backup_dir, estimate, role="backup adoption validation"
                )
            ],
        ):
            fd, raw = tempfile.mkstemp(
                prefix=".printstash-s3-adopt-", dir=settings.backup_dir
            )
            os.close(fd)
            path = Path(raw)
            try:
                with path.open("wb") as output:
                    shutil.copyfileobj(body, output)
            except Exception:
                path.unlink(missing_ok=True)
                raise
    finally:
        body.close()
    return path, response


def discover_unowned_s3_backups() -> list[dict[str, object]]:
    """Find valid tokenless archives, without making them listable."""
    target = _targets_module._get_backup_s3_target()
    if target is None:
        return []
    rows = _catalogue_module._backup_ownership_rows(bucket=target.bucket)
    committed = {
        (row.namespace, row.key, row.provider_ref)
        for row in rows
        if row.state == StorageObjectState.COMMITTED
    }
    incomplete = {
        (row.namespace, row.key)
        for row in rows
        if row.state == StorageObjectState.BLOCKED
    }
    result: list[dict[str, object]] = []
    try:
        paginator = target.client.get_paginator("list_objects_v2")
        for prefix in (
            _contracts_module._BACKUP_S3_PREFIX,
            _contracts_module._LEGACY_BACKUP_S3_PREFIX,
        ):
            for page in paginator.paginate(Bucket=target.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = str(obj.get("Key", ""))
                    name = key.rsplit("/", 1)[-1]
                    namespace = f"{target.bucket}/{prefix}"
                    if (
                        (namespace, key, target.provider_ref) in committed
                        or not name.startswith(
                            (
                                _contracts_module._BACKUP_NAME_PREFIX,
                                _contracts_module._LEGACY_BACKUP_NAME_PREFIX,
                            )
                        )
                        or not name.endswith(".tar.gz")
                    ):
                        continue
                    temp: Path | None = None
                    try:
                        head_before = target.client.head_object(
                            Bucket=target.bucket, Key=key
                        )
                        _targets_module._require_remote_identity(head_before)
                        temp, head = _download_s3_archive(
                            target,
                            key,
                            version_id=str(head_before.get("VersionId"))
                            if head_before.get("VersionId")
                            else None,
                            etag=str(head_before.get("ETag"))
                            if head_before.get("ETag")
                            else None,
                        )
                        if int(head.get("ContentLength", -1)) != int(
                            head_before.get("ContentLength", -1)
                        ):
                            raise RuntimeError("backup_remote_changed")
                        _targets_module._assert_same_s3_identity(head, head_before)
                        meta = _validate_archive_for_adoption(temp)
                        meta.id = _targets_module._backup_id_from_archive_name(name)
                        digest = _archive_format_module._sha256_path(temp)
                        if temp.stat().st_size != int(
                            head_before.get("ContentLength", -1)
                        ):
                            raise RuntimeError("backup_download_size_mismatch")
                        head_after = target.client.head_object(
                            **_targets_module._s3_identity_kwargs(
                                bucket=target.bucket, key=key, response=head_before
                            )
                        )
                        _targets_module._assert_same_s3_identity(
                            head_after, head_before
                        )
                        result.append(
                            {
                                "key": key,
                                "backup_id": meta.id,
                                "created_at": meta.created_at,
                                "size_bytes": int(
                                    head.get("ContentLength", temp.stat().st_size)
                                ),
                                "file_count": meta.file_count,
                                "storage_backend": meta.storage_backend,
                                "app_version": meta.app_version,
                                "location": "s3",
                                # The bucket/prefix namespace is required for
                                # an operator to review and authorize one
                                # exact remote locator.  It contains no
                                # credentials and is already part of the
                                # opaque source-ref derivation.
                                "namespace": namespace,
                                "prefix": prefix,
                                "archive_sha256": digest,
                                "source_ref": _targets_module.source_reference(
                                    location="s3",
                                    namespace=namespace,
                                    path=key,
                                    provider_ref=target.provider_ref,
                                ),
                                "provider_ref": target.provider_ref,
                                "candidate_kind": (
                                    "receipt_upgrade"
                                    if (namespace, key) in incomplete
                                    else "unowned_archive"
                                ),
                            }
                        )
                    except Exception:
                        logger.info(
                            "backup: unowned S3 archive failed validation: %s", key
                        )
                    finally:
                        if temp is not None:
                            temp.unlink(missing_ok=True)
    except Exception:
        logger.warning("backup: failed to discover unowned S3 backups", exc_info=True)
    return result


def _download_opendal_candidate(
    destination: _backup_destination_module.RemoteBackupDestination, key: str
) -> tuple[Path, str, CreationReceipt]:
    """Download one unowned remote archive while pinning observable identity."""
    before = destination.backend.object_info(key)
    if before is None:
        raise FileNotFoundError(key)
    with CapacityManager(get_session_factory()).hold(
        f"backup-adoption:{secrets.token_hex(12)}",
        [
            CapacityResource.for_path(
                settings.backup_dir,
                before.size,
                role="backup adoption validation",
            )
        ],
    ):
        settings.backup_dir.mkdir(parents=True, exist_ok=True)
        fd, raw = tempfile.mkstemp(
            prefix=".printstash-opendal-adopt-", dir=settings.backup_dir
        )
        os.close(fd)
        path = Path(raw)
        digest = hashlib.sha256()
        written = 0
        try:
            with (
                path.open("wb") as output,
                destination.backend.open_reader(key, expected=before) as reader,
            ):
                while chunk := reader.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
            after = destination.backend.object_info(key)
            if (
                after is None
                or written != before.size
                or after.size != before.size
                or (before.etag and after.etag != before.etag)
                or (before.version_id and after.version_id != before.version_id)
            ):
                raise RuntimeError("backup_remote_changed")
            return (
                path,
                digest.hexdigest(),
                CreationReceipt(
                    key=key,
                    size=written,
                    token=digest.hexdigest(),
                    backend=destination.backend.backend_name,
                    namespace=destination.namespace,
                    etag=after.etag,
                    version_id=after.version_id,
                    provider_ref=destination.provider_ref,
                ),
            )
        except Exception:
            path.unlink(missing_ok=True)
            raise


def discover_unowned_opendal_backups() -> list[dict[str, object]]:
    """Find validated archives under every configured OpenDAL backup root."""
    result: list[dict[str, object]] = []
    for destination in _backup_destination_module.configured_destinations():
        try:
            with get_session_factory().scoped_session() as session:
                committed = {
                    row.key
                    for row in session.exec(
                        select(OwnedStorageObject).where(
                            OwnedStorageObject.backend
                            == destination.backend.backend_name,
                            OwnedStorageObject.namespace == destination.namespace,
                            OwnedStorageObject.provider_ref == destination.provider_ref,
                            OwnedStorageObject.object_kind.in_(  # type: ignore[union-attr]
                                ("backup", "backup-legacy")
                            ),
                            OwnedStorageObject.state == StorageObjectState.COMMITTED,
                        )
                    ).all()
                }
            prefix_key = destination.key("")
            with destination.backend.iter_directory("printstash-backups") as entries:
                for entry in entries:
                    key = destination.backend.source_key(entry.key)
                    name = key.rsplit("/", 1)[-1]
                    if (
                        key in committed
                        or not _targets_module._is_direct_remote_backup_key(
                            key, prefix_key
                        )
                        or not name.startswith(
                            (
                                _contracts_module._BACKUP_NAME_PREFIX,
                                _contracts_module._LEGACY_BACKUP_NAME_PREFIX,
                            )
                        )
                        or not name.endswith(".tar.gz")
                    ):
                        continue
                    temp: Path | None = None
                    try:
                        temp, digest, receipt = _download_opendal_candidate(
                            destination, key
                        )
                        meta = _validate_archive_for_adoption(temp)
                        meta.id = _targets_module._backup_id_from_archive_name(name)
                        source_ref = _targets_module.source_reference(
                            location=destination.location,
                            namespace=destination.namespace,
                            path=key,
                            provider_ref=destination.provider_ref,
                        )
                        result.append(
                            {
                                "connection_id": destination.connection_id,
                                "connection_name": destination.name,
                                "provider": destination.provider,
                                "key": key,
                                "backup_id": meta.id,
                                "created_at": meta.created_at,
                                "size_bytes": receipt.size,
                                "file_count": meta.file_count,
                                "storage_backend": meta.storage_backend,
                                "app_version": meta.app_version,
                                "location": destination.location,
                                "namespace": destination.namespace,
                                "prefix": prefix_key,
                                "archive_sha256": digest,
                                "source_ref": source_ref,
                                "provider_ref": destination.provider_ref,
                                "candidate_kind": "unowned_archive",
                            }
                        )
                    except Exception:
                        logger.info(
                            "backup: unowned OpenDAL archive failed validation: %s",
                            key,
                        )
                    finally:
                        if temp is not None:
                            temp.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "backup: failed to discover OpenDAL backups from %s",
                destination.name,
                exc_info=True,
            )
    return result


def adopt_opendal_backup(
    connection_id: int,
    key: str,
    *,
    source_ref: str,
    expected_archive_sha256: str,
) -> _contracts_module.BackupMeta:
    """Validate and ledger-adopt one exact OpenDAL archive in place."""
    destination = next(
        (
            item
            for item in _backup_destination_module.configured_destinations()
            if item.connection_id == connection_id
        ),
        None,
    )
    if destination is None:
        raise RuntimeError("backup_remote_connection_unavailable")
    prefix_key = destination.key("")
    name = key.rsplit("/", 1)[-1]
    if (
        not _targets_module._is_direct_remote_backup_key(key, prefix_key)
        or not name.startswith(
            (
                _contracts_module._BACKUP_NAME_PREFIX,
                _contracts_module._LEGACY_BACKUP_NAME_PREFIX,
            )
        )
        or not name.endswith(".tar.gz")
    ):
        raise ValueError("backup_key_invalid")
    expected_source_ref = _targets_module.source_reference(
        location=destination.location,
        namespace=destination.namespace,
        path=key,
        provider_ref=destination.provider_ref,
    )
    if source_ref != expected_source_ref:
        raise ValueError("backup_source_ref_mismatch")
    with get_session_factory().scoped_session() as session:
        existing = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == destination.backend.backend_name,
                OwnedStorageObject.namespace == destination.namespace,
                OwnedStorageObject.key == key,
                OwnedStorageObject.provider_ref == destination.provider_ref,
                OwnedStorageObject.object_kind.in_(  # type: ignore[union-attr]
                    ("backup", "backup-legacy")
                ),
                OwnedStorageObject.state == StorageObjectState.COMMITTED,
            )
        ).first()
    if existing is not None:
        raise ValueError("backup_already_adopted")

    temp: Path | None = None
    try:
        temp, digest, receipt = _download_opendal_candidate(destination, key)
        if digest != expected_archive_sha256:
            raise RuntimeError("backup_archive_digest_mismatch")
        meta = _validate_archive_for_adoption(temp)
        meta.id = _targets_module._backup_id_from_archive_name(name)
        with get_session_factory().session() as session:
            record_creation(
                session,
                receipt,
                object_kind="backup-legacy",
                sha256=digest,
                provider_ref=destination.provider_ref,
            )
            session.commit()
        meta.path = key
        meta.location = destination.location
        meta.size_bytes = receipt.size
        meta.archive_sha256 = digest
        meta.provider_ref = destination.provider_ref
        meta.namespace = destination.namespace
        meta.source_ref = expected_source_ref
        return meta
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def adopt_s3_backup(
    key: str,
    *,
    source_ref: str | None = None,
    expected_archive_sha256: str | None = None,
) -> _contracts_module.BackupMeta:
    """Validate and ledger-adopt one remote archive without copying it."""
    if (
        not key
        or _targets_module._s3_prefix_for_key(key) is None
        or key.endswith("/")
        or not source_ref
        or not expected_archive_sha256
    ):
        raise ValueError("backup_key_invalid")
    name = key.rsplit("/", 1)[-1]
    if not name.startswith(
        (
            _contracts_module._BACKUP_NAME_PREFIX,
            _contracts_module._LEGACY_BACKUP_NAME_PREFIX,
        )
    ):
        raise ValueError("backup_key_invalid")
    target = _targets_module._get_backup_s3_target()
    if target is None:
        raise RuntimeError("backup_s3_unavailable")
    prefix = _targets_module._s3_prefix_for_key(key)
    assert prefix is not None
    namespace = f"{target.bucket}/{prefix}"
    with get_session_factory().session() as session:
        existing = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.backend == "backup-s3",
                OwnedStorageObject.namespace == namespace,
                OwnedStorageObject.key == key,
                (
                    (OwnedStorageObject.provider_ref == target.provider_ref)
                    | OwnedStorageObject.provider_ref.is_(None)  # type: ignore[union-attr]
                ),
            )
        ).first()
        if existing is not None and existing.state == StorageObjectState.COMMITTED:
            if existing.provider_ref not in (None, target.provider_ref):
                raise ValueError("backup_provider_identity_mismatch")
            if existing.sha256 is not None:
                raise ValueError("backup_already_adopted")
        if existing is not None and existing.state == StorageObjectState.PENDING:
            raise ValueError("backup_already_adopted")
    temp: Path | None = None
    try:
        head_before = target.client.head_object(Bucket=target.bucket, Key=key)
        _targets_module._require_remote_identity(head_before)
        candidate_ref = _targets_module.source_reference(
            location="s3",
            namespace=namespace,
            path=key,
            provider_ref=target.provider_ref,
        )
        if source_ref != candidate_ref:
            raise ValueError("backup_source_ref_mismatch")
        temp, head = _download_s3_archive(
            target,
            key,
            version_id=str(head_before.get("VersionId"))
            if head_before.get("VersionId")
            else None,
            etag=str(head_before.get("ETag")) if head_before.get("ETag") else None,
        )
        _targets_module._require_remote_identity(head)
        if int(head.get("ContentLength", -1)) != int(
            head_before.get("ContentLength", -1)
        ):
            raise RuntimeError("backup_remote_changed")
        _targets_module._assert_same_s3_identity(head, head_before)
        meta = _validate_archive_for_adoption(temp)
        meta.id = _targets_module._backup_id_from_archive_name(name)
        digest = _archive_format_module._sha256_path(temp)
        if digest != expected_archive_sha256:
            raise RuntimeError("backup_archive_digest_mismatch")
        size = int(head.get("ContentLength", temp.stat().st_size))
        if size != temp.stat().st_size:
            raise RuntimeError("backup_download_size_mismatch")
        receipt = CreationReceipt(
            key=key,
            size=size,
            # Historical objects have no trusted create token. The complete
            # content digest is the adoption token; verification below uses
            # immutable size/hash/eTag/version evidence instead of metadata.
            token=digest,
            backend="backup-s3",
            namespace=namespace,
            etag=str(head["ETag"]) if head.get("ETag") else None,
            version_id=str(head["VersionId"]) if head.get("VersionId") else None,
        )
        head_after = target.client.head_object(
            **_targets_module._s3_identity_kwargs(
                bucket=target.bucket, key=key, response=head
            )
        )
        _targets_module._assert_same_s3_identity(head_after, head)
        with get_session_factory().session() as session:
            record_creation(
                session,
                receipt,
                object_kind="backup-legacy",
                sha256=digest,
                provider_ref=target.provider_ref,
                upgrade_provider_ref=True,
            )
            session.commit()
        meta.path = key
        meta.location = "s3"
        meta.archive_sha256 = digest
        meta.provider_ref = target.provider_ref
        meta.namespace = namespace
        meta.source_ref = _targets_module.source_reference(
            location="s3",
            namespace=namespace,
            path=key,
            provider_ref=target.provider_ref,
        )
        return meta
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def adopt_local_backup(filename: str) -> _contracts_module.BackupMeta:
    """Explicitly adopt one validated legacy archive into the ownership ledger."""
    if not filename or Path(filename).name != filename:
        raise ValueError("backup_filename_invalid")
    if not filename.startswith(
        (
            _contracts_module._BACKUP_NAME_PREFIX,
            _contracts_module._LEGACY_BACKUP_NAME_PREFIX,
        )
    ):
        raise ValueError("backup_filename_invalid")
    root = settings.backup_dir.expanduser().resolve(strict=False)
    archive = (root / filename).resolve(strict=False)
    if archive.parent != root or not archive.is_file():
        raise FileNotFoundError(filename)
    meta = _validate_archive_for_adoption(archive)
    backend = LocalStorageBackend()
    digest = _archive_format_module._sha256_path(archive)
    receipt = backend.adopt_existing(
        str(archive), expected_size=archive.stat().st_size, expected_sha256=digest
    )
    with get_session_factory().session() as session:
        record_creation(
            session,
            receipt,
            object_kind="backup",
            sha256=digest,
            provider_ref=provider_ref_for_backend(
                backend, namespace=backend.namespace_for(str(archive))
            ),
            upgrade_provider_ref=True,
        )
        session.commit()
    meta.path = str(archive)
    meta.location = "local"
    meta.archive_sha256 = digest
    meta.namespace = backend.namespace_for(str(archive))
    meta.provider_ref = provider_ref_for_backend(backend, namespace=meta.namespace)
    meta.source_ref = _targets_module.source_reference(
        location="local",
        namespace=meta.namespace,
        path=meta.path,
        provider_ref=meta.provider_ref,
    )
    return meta


@exclusive_backup_operation
def upload_backup_archive(
    filename: str, source: BinaryIO, *, expected_size: int | None = None
) -> _contracts_module.BackupMeta:
    """Validate and register a backup archive uploaded by an administrator."""
    estimate = (
        expected_size
        if expected_size is not None and 0 <= expected_size <= settings.max_upload_bytes
        else settings.max_upload_bytes
    )
    with CapacityManager(get_session_factory()).hold(
        f"backup-upload:{secrets.token_hex(12)}",
        [
            CapacityResource.for_path(
                settings.backup_dir, estimate, role="backup upload staging"
            )
        ],
    ):
        return _upload_backup_archive(filename, source)


def _upload_backup_archive(
    filename: str, source: BinaryIO
) -> _contracts_module.BackupMeta:
    if not filename or "\\" in filename or Path(filename).name != filename:
        raise ValueError("backup_filename_invalid")
    if not filename.startswith(
        (
            _contracts_module._BACKUP_NAME_PREFIX,
            _contracts_module._LEGACY_BACKUP_NAME_PREFIX,
        )
    ):
        raise ValueError("backup_filename_invalid")
    backup_id = _targets_module._backup_id_from_archive_name(filename)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    archive_path = settings.backup_dir / filename
    if archive_path.exists():
        raise FileExistsError("backup_already_exists")
    fd, raw_staged = tempfile.mkstemp(
        prefix=".printstash-backup-upload-", dir=settings.backup_dir
    )
    os.close(fd)
    staged = Path(raw_staged)
    staged.unlink()
    digest = hashlib.sha256()
    try:
        storage.stream_to_path(
            source,
            staged,
            max_bytes=settings.max_upload_bytes,
            digest=digest,
        )
        meta = _validate_archive_for_adoption(staged)
        backend = LocalStorageBackend()
        with get_session_factory().session() as session:
            publication = prepare_file(
                session,
                backend,
                str(archive_path),
                staged,
                object_kind="backup",
                sha256=digest.hexdigest(),
                move=True,
            )
            receipt = publication.receipt
            audit.record(
                session,
                action="backup.upload",
                resource_type="backup",
                diff={"backup_id": backup_id, "size_bytes": receipt.size},
            )
            session.flush()
            adopt_publication(session, publication)
            session.commit()
        namespace = backend.namespace_for(str(archive_path))
        provider_ref = provider_ref_for_backend(backend, namespace=namespace)
        meta.id = backup_id
        meta.path = str(archive_path)
        meta.location = "local"
        meta.size_bytes = receipt.size
        meta.archive_sha256 = digest.hexdigest()
        meta.provider_ref = provider_ref
        meta.namespace = namespace
        meta.source_ref = _targets_module.source_reference(
            location="local",
            namespace=namespace,
            path=str(archive_path),
            provider_ref=provider_ref,
        )
        return meta
    finally:
        staged.unlink(missing_ok=True)


def discover_unowned_local_backups() -> list[dict[str, object]]:
    """Describe valid legacy archives awaiting explicit administrator adoption.

    Normal listing remains ownership-only.  This bounded operator view exposes
    only archives that pass the complete manifest, namespace, member, and
    SQLite validation used by adoption; malformed candidates are logged but
    never returned as actionable backups.
    """
    root = settings.backup_dir.expanduser().resolve(strict=False)
    if not root.is_dir():
        return []
    local = LocalStorageBackend()
    local_namespace = f"backup:{root}"
    committed = _catalogue_module._committed_backup_keys(
        "local",
        provider_ref=provider_ref_for_backend(local, namespace=local_namespace),
    )
    candidates: list[dict[str, object]] = []
    for archive in sorted(root.glob("*.tar.gz")):
        if str(archive) in committed or not archive.name.startswith(
            (
                _contracts_module._BACKUP_NAME_PREFIX,
                _contracts_module._LEGACY_BACKUP_NAME_PREFIX,
            )
        ):
            continue
        try:
            meta = _validate_archive_for_adoption(archive)
        except Exception:
            logger.info(
                "backup: unowned archive failed adoption validation: %s", archive
            )
            continue
        candidates.append(
            {
                "filename": archive.name,
                "backup_id": meta.id,
                "created_at": meta.created_at,
                "size_bytes": meta.size_bytes,
                "file_count": meta.file_count,
                "storage_backend": meta.storage_backend,
                "app_version": meta.app_version,
                "location": "local",
                "namespace": local_namespace,
                "provider_ref": provider_ref_for_backend(
                    local, namespace=local_namespace
                ),
            }
        )
    return candidates
