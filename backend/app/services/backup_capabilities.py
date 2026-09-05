"""Explain available actions for an exact backup source without mutating it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from app.db.models import OwnedStorageObject, StorageObjectState
from app.db.session import get_session_factory
from app.services.backup_destination import destination_for_ownership
from app.services.storage_operations import replica_operations, serialize_operations

if TYPE_CHECKING:
    from app.services.backup import BackupMeta


def backup_operations(meta: BackupMeta) -> dict[str, dict]:
    exact_delete = False
    with get_session_factory().scoped_session() as session:
        row = session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.provider_ref == meta.provider_ref,
                OwnedStorageObject.namespace == meta.namespace,
                OwnedStorageObject.key == meta.path,
                OwnedStorageObject.object_kind.in_(("backup", "backup-legacy")),
                OwnedStorageObject.state == StorageObjectState.COMMITTED,
            )
        ).first()
        if row is not None and row.token and row.sha256:
            if meta.location == "local":
                exact_delete = row.device is not None and row.inode is not None
            elif meta.location == "s3":
                exact_delete = bool(
                    (row.version_id and row.version_id != "null") or row.etag
                )
            elif meta.location.startswith("opendal:"):
                destination = destination_for_ownership(row)
                exact_delete = bool(
                    destination is not None
                    and row.version_id
                    and row.version_id != "null"
                    and getattr(
                        destination.backend.operator_capabilities,
                        "delete_with_version",
                        False,
                    )
                )
    gc_reason = "storage_gc_witness_unsupported"
    if meta.location in {"s3", "opendal:s3"}:
        from app.services.gc_planner import _source_identity_evidence

        gc_reason = (
            "storage_gc_verification_required"
            if _source_identity_evidence(meta) is not None
            else "storage_independent_backup_required"
        )
    return serialize_operations(
        replica_operations(exact_delete=exact_delete, gc_reason=gc_reason)
    )
