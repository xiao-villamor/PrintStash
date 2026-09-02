"""The single source of truth for "which blobs does the database still own?".

Backup manifests and vault audits both need a complete, identical answer to
this question.  The census is deliberately read-only: destructive maintenance
never treats absence from this snapshot as evidence that a discovered file is
safe to delete.  The census used to cover ``File.path`` alone, which made every
Document blob appear unowned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import (
    SENTINEL_FILE_HASH,
    Collection,
    Document,
    File,
    ModelSourceCover,
    MultipartModel,
    ThumbnailGeneration,
)
from app.services.storage_backend import get_backend

_COLLECTION_IMAGE_RE = re.compile(r"/collections/(\d+)/images/([^\s)\]?#]+)")
_DOCUMENT_IMAGE_RE = re.compile(r"/documents/(\d+)/images/([^\s)\]?#]+)")


@dataclass(frozen=True)
class OwnedBlob:
    key: str
    resource_type: str
    resource_id: int
    expected_size: int | None = None
    expected_sha256: str | None = None
    display_name: str | None = None


@dataclass
class StorageOwnershipSnapshot:
    primary: list[OwnedBlob] = field(default_factory=list)
    external: list[OwnedBlob] = field(default_factory=list)
    derived: list[OwnedBlob] = field(default_factory=list)
    embedded: list[OwnedBlob] = field(default_factory=list)
    discovered_keys: set[str] = field(default_factory=set)

    @property
    def claimed_keys(self) -> set[str]:
        return {
            blob.key
            for group in (self.primary, self.derived, self.embedded)
            for blob in group
        }


def ownership_snapshot(
    session: Session, *, discover: bool = True
) -> StorageOwnershipSnapshot:
    """Typed census for audit/backup; never used to widen trash deletion."""
    backend = get_backend()
    result = StorageOwnershipSnapshot()

    files = list(session.exec(select(File)).all())
    generation_keys: dict[int, set[str]] = {}
    for generation in session.exec(select(ThumbnailGeneration)).all():
        if generation.storage_key:
            generation_keys.setdefault(generation.file_id, set()).add(
                generation.storage_key
            )
    for row in files:
        if row.id is None:
            continue
        # External print jobs use a database-only placeholder whose path is
        # /dev/null. It is not a vault blob and must never enter backup,
        # restore, or audit ownership sets. Match the reserved hash as well as
        # the path so a real missing vault artifact is still surfaced by
        # backup.stat_size() rather than silently omitted.
        if row.path == "/dev/null" and row.sha256 == SENTINEL_FILE_HASH:
            continue
        blob = OwnedBlob(
            key=row.path,
            resource_type="file",
            resource_id=row.id,
            expected_size=row.size_bytes,
            expected_sha256=row.sha256,
            display_name=row.original_filename,
        )
        (result.external if row.is_external else result.primary).append(blob)
        current_thumbnail = row.thumbnail_path or backend.thumbnail_key(row.id)
        result.derived.append(
            OwnedBlob(
                key=current_thumbnail,
                resource_type="thumbnail",
                resource_id=row.id,
                display_name=row.original_filename,
            )
        )
        if current_thumbnail != backend.thumbnail_key(row.id):
            result.derived.append(
                OwnedBlob(
                    key=backend.thumbnail_key(row.id),
                    resource_type="legacy_webp_thumbnail",
                    resource_id=row.id,
                    display_name=row.original_filename,
                )
            )
        for generation_key in sorted(generation_keys.get(row.id, set())):
            if generation_key == current_thumbnail:
                continue
            result.derived.append(
                OwnedBlob(
                    key=generation_key,
                    resource_type="thumbnail_generation",
                    resource_id=row.id,
                    display_name=row.original_filename,
                )
            )
        result.derived.append(
            OwnedBlob(
                key=backend.legacy_thumbnail_key(row.id),
                resource_type="legacy_thumbnail",
                resource_id=row.id,
                display_name=row.original_filename,
            )
        )
        if row.sha256:
            result.derived.append(
                OwnedBlob(
                    key=backend.stl_cache_key(row.sha256),
                    resource_type="stl_cache",
                    resource_id=row.id,
                    display_name=row.original_filename,
                )
            )

    documents = list(session.exec(select(Document)).all())
    for row in documents:
        if row.id is None:
            continue
        if row.filename:
            result.primary.append(
                OwnedBlob(
                    key=backend.document_file_key(row.id, row.filename),
                    resource_type="document",
                    resource_id=row.id,
                    expected_size=row.size_bytes,
                    expected_sha256=row.sha256,
                    display_name=row.filename,
                )
            )
        for doc_id, name in _DOCUMENT_IMAGE_RE.findall(row.body or ""):
            if int(doc_id) == row.id:
                result.embedded.append(
                    OwnedBlob(
                        key=backend.document_image_key(row.id, name),
                        resource_type="document_image",
                        resource_id=row.id,
                        display_name=name,
                    )
                )

    for row in session.exec(select(Collection)).all():
        if row.id is None:
            continue
        for collection_id, name in _COLLECTION_IMAGE_RE.findall(row.readme or ""):
            if int(collection_id) == row.id:
                result.embedded.append(
                    OwnedBlob(
                        key=backend.collection_image_key(row.id, name),
                        resource_type="collection_image",
                        resource_id=row.id,
                        display_name=name,
                    )
                )

    # Provenance covers are vault-owned blobs even though they are not attached
    # to a File.  Keep them in the census so backup/restore cannot silently lose
    # the private representative image.
    for row in session.exec(select(ModelSourceCover)).all():
        result.primary.append(
            OwnedBlob(
                key=row.storage_key,
                resource_type="model_source_cover",
                resource_id=row.id or 0,
                expected_size=row.size_bytes,
                display_name="source-cover.webp",
            )
        )
    for row in session.exec(select(MultipartModel)).all():
        if row.id is None or row.cover_filename is None:
            continue
        result.primary.append(
            OwnedBlob(
                key=backend.multipart_model_cover_key(row.id, row.cover_filename),
                resource_type="multipart_model_cover",
                resource_id=row.id,
                expected_size=row.cover_size_bytes,
                display_name="multipart-cover.webp",
            )
        )
    if discover:
        result.discovered_keys.update(backend.walk_keys())
        # Local storage keeps derived objects under a separate root. S3's
        # default walk already covers every vault-data prefix.
        if backend.direct_path(backend.thumbnail_key(0)) is not None:
            result.discovered_keys.update(backend.walk_keys(str(settings.thumb_dir)))
    return result


def all_owned_blob_keys(session: Session) -> set[str]:
    """Every storage key a DB row lays claim to, across all owning tables.

    Trashed rows are included on purpose: their bytes must survive until the
    row is hard-deleted, otherwise restoring from trash yields an empty file.

    Includes derived and embedded keys for backup/audit completeness. External
    paths remain included for compatibility even though their bytes are always
    user-owned.
    """
    snapshot = ownership_snapshot(session, discover=False)
    # Compatibility contract: external File.path values historically appeared
    # here even though trash and backup callers separately avoid deleting them.
    return snapshot.claimed_keys | {blob.key for blob in snapshot.external}
