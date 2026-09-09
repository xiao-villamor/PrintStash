"""Complete owned-key translation for a Vault migration, excluding linked sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlmodel import Session, select

from app.db.models import ArtifactUploadSession, CaptureUploadSlot, Model, StagingLease
from app.modules.ingestion.artifact_uploads.native_parts import (
    NativeMultipartUploadAdapter,
)
from app.modules.storage.migration_identity import namespace_ref
from app.modules.storage.storage_backend.contracts import StorageBackend
from app.modules.storage.storage_utils import ownership_snapshot


@dataclass(frozen=True)
class MigrationBlob:
    source_key: str
    destination_key: str
    resource_type: str
    resource_id: str
    expected_size: int | None
    expected_sha256: str | None


def remap_owned_key(
    source: StorageBackend, destination: StorageBackend, key: str
) -> str:
    """Translate only caller-proven owned keys through adapter key constructors."""

    def prefixes(backend: StorageBackend) -> list[str]:
        return [
            backend.blob_key("__migration__", 1, "__object__")[
                : -len("__migration__/v1/__object__")
            ],
            backend.thumbnail_key(0)[: -len("0.webp")],
            backend.source_cover_key(0)[: -len("0.webp")],
            backend.capture_upload_slot_key("__object__")[: -len("__object__")],
            backend.stl_cache_key("__hash__")[: -len("__hash__.stl")],
            backend.collection_image_key(0, "__object__")[: -len("0/__object__")],
            backend.document_file_key(0, "__object__")[: -len("0/__object__")],
            backend.document_image_key(0, "__object__")[: -len("0/__object__")],
            backend.multipart_model_cover_key(0, "__object__")[: -len("0/__object__")],
        ]

    pairs = sorted(
        zip(prefixes(source), prefixes(destination), strict=True),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    for old, new in pairs:
        if key.startswith(old):
            suffix = key[len(old) :]
            if not suffix or any(part in {".", ".."} for part in suffix.split("/")):
                raise ValueError("migration_key_invalid")
            result = new + PurePosixPath(suffix).as_posix()
            destination.validate_restore_key(result)
            return result
    raise ValueError("migration_owned_key_outside_namespace")


def census(
    session: Session, source: StorageBackend, destination: StorageBackend
) -> list[MigrationBlob]:
    snapshot = ownership_snapshot(session, discover=False, backend=source)
    result: dict[str, MigrationBlob] = {}
    for group in (snapshot.primary, snapshot.derived, snapshot.embedded):
        for blob in group:
            if not source.exists(blob.key):
                if group is snapshot.primary or group is snapshot.embedded:
                    raise ValueError("migration_source_object_missing")
                continue
            result[blob.key] = MigrationBlob(
                blob.key,
                remap_owned_key(source, destination, blob.key),
                blob.resource_type,
                str(blob.resource_id),
                blob.expected_size,
                blob.expected_sha256,
            )
    # Local ingestion scratch paths remain with their lease owner. Published
    # capture bytes and in-flight publication destinations are inside the Vault
    # and must move even when the domain transaction has not attached a File.
    for slot in session.exec(select(CaptureUploadSlot)).all():
        if slot.storage_key and source.exists(slot.storage_key):
            result[slot.storage_key] = MigrationBlob(
                slot.storage_key,
                remap_owned_key(source, destination, slot.storage_key),
                "capture_upload_slot",
                slot.id,
                slot.size_bytes,
                slot.sha256,
            )
    for model in session.exec(select(Model)).all():
        if model.thumbnail_path and source.exists(model.thumbnail_path):
            result.setdefault(
                model.thumbnail_path,
                MigrationBlob(
                    model.thumbnail_path,
                    remap_owned_key(source, destination, model.thumbnail_path),
                    "model_thumbnail",
                    str(model.id),
                    None,
                    None,
                ),
            )
    for upload in session.exec(select(ArtifactUploadSession)).all():
        # Incomplete multipart uploads have no addressable object; their owner
        # and old epoch remain retained, so neither census nor cleanup adopts it.
        receipt = NativeMultipartUploadAdapter.completion_receipt(upload)
        if (
            receipt
            and source.storage_target
            and upload.destination_ref
            in {source.storage_target.target_ref, namespace_ref(source)}
        ):
            if not source.exists(receipt.key):
                if upload.state not in {"completed", "aborted", "expired", "failed"}:
                    raise ValueError("migration_source_object_missing")
                continue
            result[receipt.key] = MigrationBlob(
                receipt.key,
                destination.capture_upload_slot_key("artifact-" + upload.id),
                "artifact_upload_staging",
                upload.id,
                upload.declared_size,
                upload.verified_sha256 or upload.client_sha256,
            )
    for lease in session.exec(select(StagingLease)).all():
        if lease.destination_key and source.exists(lease.destination_key):
            result.setdefault(
                lease.destination_key,
                MigrationBlob(
                    lease.destination_key,
                    remap_owned_key(source, destination, lease.destination_key),
                    "staging_publication",
                    lease.id,
                    lease.size_bytes,
                    lease.sha256,
                ),
            )
    return sorted(result.values(), key=lambda blob: blob.source_key)
