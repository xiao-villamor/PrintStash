"""Upload epoch fencing and retained multipart cleanup after Vault activation.

Local API chunks are independent of the Vault and survive unchanged. Completed
native staging objects move with their exact receipts. Incomplete native uploads
cannot be transplanted between providers: fence them, retaining their original
handle and provider solely for owner-scoped abort/expiry cleanup.
"""

import json

from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import ArtifactUploadSession, VaultMigrationObject, VaultMigrationRun
from app.modules.ingestion.artifact_uploads.native_parts import (
    NativeMultipartUploadAdapter,
)
from app.modules.storage.migration_identity import namespace_ref
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    StorageBackend,
)
from app.modules.storage.storage_backend.factory import build_configured_backend
from app.modules.storage.storage_providers import parse_provider_config


def activate_uploads(
    session: Session,
    objects: list[VaultMigrationObject],
    *,
    source: StorageBackend,
    destination: StorageBackend,
) -> None:
    assert source.storage_target is not None and destination.storage_target is not None
    moved = {obj.source_key: obj for obj in objects}
    for upload in session.exec(select(ArtifactUploadSession)).all():
        if upload.adapter_id != "native_parts" or upload.destination_ref not in {
            source.storage_target.target_ref,
            namespace_ref(source),
        }:
            continue
        receipt = NativeMultipartUploadAdapter.completion_receipt(upload)
        obj = moved.get(receipt.key) if receipt else None
        if obj and obj.destination_receipt:
            NativeMultipartUploadAdapter.relocate_completion(
                upload, CreationReceipt(**json.loads(obj.destination_receipt))
            )
            upload.destination_ref = namespace_ref(destination)
        elif upload.state not in {"completed", "aborted", "expired", "failed"}:
            upload.destination_ref = namespace_ref(source)
            upload.state = "failed"
            upload.error_code = "artifact_upload_vault_generation_changed"
            upload.retryable = False
        else:
            continue
        upload.version += 1
        upload.updated_at = utcnow()
        session.add(upload)


def retained_upload_backend(session: Session, destination_ref: str) -> StorageBackend:
    """Resolve only a durably retained old owner, never a read fallback."""
    runs = session.exec(
        select(VaultMigrationRun)
        .where(
            VaultMigrationRun.activated_at.is_not(None),
        )
        .order_by(VaultMigrationRun.activated_at.desc())
    ).all()
    for run in runs:
        if run.source_config == "{}":
            continue
        backend = build_configured_backend(
            parse_provider_config(json.loads(run.source_config))
        )
        if backend.storage_target and destination_ref in {
            backend.storage_target.target_ref,
            namespace_ref(backend),
        }:
            return backend
    raise ValueError("artifact_upload_retained_provider_unavailable")
