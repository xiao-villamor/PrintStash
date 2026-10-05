"""Owned backup publication receipts and uncertain-commit reconciliation."""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

from sqlmodel import select

import app.modules.backups.backup.caches as _caches_module
import app.modules.backups.backup.snapshot as _snapshot_module
import app.modules.backups.backup.targets as _targets_module
import app.modules.backups.backup_destination as _backup_destination_module
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    OwnedStorageObject,
    StorageObjectState,
)
from app.db.session import get_session_factory
from app.modules.storage.storage_backend.contracts import CreationReceipt
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_ownership import (
    complete_publication,
    provider_ref_for_backend,
    settle_observed_publication,
)
from app.modules.storage.storage_publication import PublicationReservation
from app.runtime.maintenance import exclusive_backup_operation

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------


@exclusive_backup_operation
def reconcile_backup_publications(
    limit: int = 100, *, ownership_id: int | None = None
) -> int:
    """Finish or block backup reservations left across a publication crash."""
    # Cache projections have a separate lifecycle from remote backup
    # publications.  Reconcile them before touching backup rows so an absent
    # cloud target can never make a cache row look like a root backup.
    _caches_module.reconcile_backup_caches(limit=limit)
    reconciled = 0
    with get_session_factory().session() as session:
        statement = select(OwnedStorageObject).where(
            OwnedStorageObject.object_kind == "backup",
            OwnedStorageObject.state == StorageObjectState.PENDING,
        )
        if ownership_id is not None:
            statement = statement.where(OwnedStorageObject.id == ownership_id)
        pending = session.exec(
            statement.order_by(OwnedStorageObject.id.asc()).limit(limit)
        ).all()
        target = _targets_module._get_backup_s3_target()
        s3 = target.client if target else None
        bucket = target.bucket if target else ""
        for row in pending:
            publication_reservation = PublicationReservation.of(row)

            def settle(
                state,
                error,
                *,
                reservation=publication_reservation,
            ):
                settle_observed_publication(
                    session,
                    reservation,
                    observed_state=StorageObjectState.PENDING,
                    state=state,
                    last_error=error,
                )
                session.commit()

            receipt: CreationReceipt | None = None
            try:
                if row.backend == "local":
                    if row.size_bytes is None or row.sha256 is None:
                        raise RuntimeError("backup_publication_evidence_missing")
                    receipt = LocalStorageBackend().adopt_existing(
                        row.key,
                        expected_size=row.size_bytes,
                        expected_sha256=row.sha256,
                    )
                elif row.backend == "backup-s3" and s3 is not None:
                    # A config flip must not probe a new bucket using an old
                    # pending row's key. Leave the old target retryable until
                    # the administrator restores that exact configuration.
                    row_bucket = row.namespace.split("/", 1)[0]
                    if not bucket:
                        # Compatibility for injected test clients; a real
                        # configured target always carries its bucket.
                        bucket = row_bucket
                        target = replace(target, bucket=bucket)
                        s3 = target.client
                    if row_bucket != bucket:
                        continue
                    if row.provider_ref and row.provider_ref != target.provider_ref:
                        settle(
                            StorageObjectState.PENDING,
                            "retryable:backup_target_changed",
                        )
                        continue
                    # A reservation is intentionally written before PUT, so it
                    # has no remote ETag/version yet.  This one reconciliation
                    # probe is the only permitted unconditional S3 read: the
                    # row's persisted namespace selects the bucket, and the
                    # response supplies the immutable identity used for every
                    # subsequent conditional operation.
                    response = s3.head_object(Bucket=bucket, Key=row.key)
                    metadata = response.get("Metadata", {})
                    if (
                        not row.token
                        or not row.sha256
                        or metadata.get("printstash-create-token") != row.token
                        or int(response.get("ContentLength", -1)) != row.size_bytes
                        or not response.get("VersionId")
                        and not response.get("ETag")
                    ):
                        raise RuntimeError("backup_publication_evidence_mismatch")
                    # HEAD supplies only identity/size metadata.  It has no
                    # response body, so fetch the exact object separately for
                    # the digest and archive validation proof.
                    get_kwargs: dict[str, str] = {
                        "Bucket": bucket,
                        "Key": row.key,
                    }
                    if response.get("VersionId"):
                        get_kwargs["VersionId"] = str(response["VersionId"])
                    else:
                        get_kwargs["IfMatch"] = str(response["ETag"])
                    object_response = s3.get_object(**get_kwargs)
                    fd, raw_name = tempfile.mkstemp(
                        prefix=".printstash-backup-reconcile-",
                        dir=settings.backup_dir,
                    )
                    os.close(fd)
                    candidate = Path(raw_name)
                    try:
                        digest = hashlib.sha256()
                        body = object_response["Body"]
                        try:
                            with candidate.open("wb") as output:
                                while chunk := body.read(1024 * 1024):
                                    digest.update(chunk)
                                    output.write(chunk)
                        finally:
                            body.close()
                        if digest.hexdigest() != row.sha256:
                            raise RuntimeError("backup_publication_digest_mismatch")
                        _snapshot_module._validate_created_archive_payload(candidate)
                    finally:
                        candidate.unlink(missing_ok=True)
                    confirm_kwargs: dict[str, str] = {
                        "Bucket": bucket,
                        "Key": row.key,
                    }
                    if response.get("VersionId"):
                        confirm_kwargs["VersionId"] = str(response["VersionId"])
                    else:
                        confirm_kwargs["IfMatch"] = str(response["ETag"])
                    confirmed = s3.head_object(**confirm_kwargs)
                    if confirmed.get("VersionId") != response.get(
                        "VersionId"
                    ) or confirmed.get("ETag") != response.get("ETag"):
                        raise RuntimeError("backup_publication_evidence_mismatch")
                    receipt = CreationReceipt(
                        key=row.key,
                        size=int(response["ContentLength"]),
                        token=row.token,
                        backend="backup-s3",
                        namespace=row.namespace,
                        etag=str(response.get("ETag"))
                        if response.get("ETag")
                        else None,
                        version_id=(
                            str(response.get("VersionId"))
                            if response.get("VersionId")
                            else None
                        ),
                    )
                elif row.backend.startswith("backup-opendal-"):
                    destination = _backup_destination_module.destination_for_ownership(
                        row
                    )
                    if destination is None:
                        settle(
                            StorageObjectState.PENDING,
                            "retryable:backup_target_changed",
                        )
                        continue
                    info = destination.backend.object_info(row.key)
                    if (
                        info is None
                        or row.size_bytes is None
                        or row.sha256 is None
                        or info.size != row.size_bytes
                    ):
                        raise RuntimeError("backup_publication_evidence_mismatch")
                    if not row.token and info.version_id in (None, "", "null"):
                        # An interrupted creator has no operation receipt. Size
                        # and digest alone cannot name a physical generation.
                        # Keep its durable reservation for explicit recovery;
                        # never fabricate a blank token or cleanup authority.
                        settle(
                            StorageObjectState.PENDING,
                            "retryable:backup_publication_evidence_missing",
                        )
                        continue
                    fd, raw_name = tempfile.mkstemp(
                        prefix=".printstash-backup-reconcile-",
                        dir=settings.backup_dir,
                    )
                    os.close(fd)
                    candidate = Path(raw_name)
                    candidate.unlink()
                    try:
                        committed = OwnedStorageObject.model_validate(row.model_dump())
                        committed.state = StorageObjectState.COMMITTED
                        committed.etag = info.etag
                        committed.version_id = info.version_id
                        destination.download_owned(committed, candidate)
                        _snapshot_module._validate_created_archive_payload(candidate)
                    finally:
                        candidate.unlink(missing_ok=True)
                    receipt = CreationReceipt(
                        key=row.key,
                        size=info.size,
                        token=row.token if row.token else uuid.uuid4().hex,
                        backend=row.backend,
                        namespace=row.namespace,
                        etag=info.etag,
                        version_id=info.version_id,
                        provider_ref=row.provider_ref,
                    )
                else:
                    if row.backend == "backup-s3" or row.backend.startswith(
                        "backup-opendal-"
                    ):
                        # A provider outage is not evidence that the
                        # publication is corrupt. Keep the reservation pending
                        # so a later reconciliation can prove this exact
                        # namespace rather than making it permanently blocked.
                        settle(
                            StorageObjectState.PENDING,
                            "retryable:backup_provider_unavailable",
                        )
                        continue
                    raise RuntimeError("backup_publication_backend_unavailable")
                assert receipt is not None
                complete_publication(
                    session,
                    publication_reservation,
                    receipt,
                    object_kind="backup",
                    sha256=row.sha256,
                    provider_ref=(row.provider_ref or target.provider_ref)
                    if row.backend == "backup-s3"
                    else row.provider_ref
                    if row.backend.startswith("backup-opendal-")
                    else provider_ref_for_backend(
                        LocalStorageBackend(), namespace=row.namespace
                    ),
                )
                reconciled += 1
            except RuntimeError as exc:
                settle(StorageObjectState.BLOCKED, type(exc).__name__[:255])
            except Exception as exc:
                # A provider outage is retryable; do not turn an unavailable
                # S3 endpoint into a permanent operator decision.
                settle(
                    StorageObjectState.PENDING, f"retryable:{type(exc).__name__}"[:255]
                )
            session.commit()
        session.commit()
    return reconciled
