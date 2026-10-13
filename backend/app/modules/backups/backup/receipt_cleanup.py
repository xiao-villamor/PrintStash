"""Exact backup receipt reclamation after durable storage retirement.

Credentials/connection availability remain with backup owners. The shared
outbox supplies immutable evidence; this owner never changes a receipt's
provider binding or falls back to a path-only delete.
"""

from __future__ import annotations

from dataclasses import replace

from botocore.exceptions import ClientError

from app.db.models import OwnedStorageObject, StorageObjectState
from app.modules.backups import backup_destination
from app.modules.backups.backup import targets
from app.modules.storage.storage_backend.contracts import CreationReceipt
from app.modules.storage.storage_publication import ReceiptReclaimResult


def reclaim_receipt(receipt: CreationReceipt) -> ReceiptReclaimResult:
    if receipt.backend == "backup-s3":
        target = targets._get_backup_s3_target()
        if target is None:
            raise RuntimeError("backup_delete_provider_unavailable")
        bucket = receipt.namespace.split("/", 1)[0]
        if not target.bucket:
            target = replace(target, bucket=bucket)
        if target.bucket != bucket or target.provider_ref != receipt.provider_ref:
            return ReceiptReclaimResult.PROVIDER_MISMATCH
        if not receipt.version_id or receipt.version_id == "null":
            return ReceiptReclaimResult.UNSUPPORTED
        kwargs: dict[str, str] = {"Bucket": bucket, "Key": receipt.key}
        kwargs["VersionId"] = receipt.version_id
        try:
            target.client.delete_object(**kwargs)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "NoSuchVersion", "404", "NotFound"}:
                return ReceiptReclaimResult.ABSENT
            if code in {"PreconditionFailed", "412"}:
                return ReceiptReclaimResult.MISMATCH
            raise
        return ReceiptReclaimResult.REMOVED
    if receipt.backend.startswith("backup-opendal-"):
        # Only binding fields are used to locate the exact saved destination.
        binding = OwnedStorageObject(
            backend=receipt.backend,
            namespace=receipt.namespace,
            key=receipt.key,
            provider_ref=receipt.provider_ref,
            object_kind="backup",
            state=StorageObjectState.RETIRING,
        )
        destination = backup_destination.destination_for_ownership(binding)
        if destination is None:
            raise RuntimeError("backup_delete_provider_unavailable")
        if not destination.can_delete_receipt(receipt):
            return ReceiptReclaimResult.UNSUPPORTED
        return (
            ReceiptReclaimResult.REMOVED
            if destination.delete_receipt(receipt)
            else ReceiptReclaimResult.UNSUPPORTED
        )
    raise ValueError("backup_delete_backend_unsupported")
