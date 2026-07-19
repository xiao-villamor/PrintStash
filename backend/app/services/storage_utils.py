"""The single source of truth for "which blobs does the database still own?".

Both the orphan-blob GC (``services.trash``) and the backup manifest
(``services.backup``) have to answer this question, and they must answer it
identically: a key the GC believes is orphaned gets deleted, and a key the
backup misses is silently absent from the archive. They used to census
``File.path`` alone, which made every Document blob look unowned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import Collection, Document, File
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


def ownership_snapshot(session: Session, *, discover: bool = True) -> StorageOwnershipSnapshot:
    """Typed census for audit/backup; never used to widen trash deletion."""
    backend = get_backend()
    result = StorageOwnershipSnapshot()

    files = list(session.exec(select(File)).all())
    for row in files:
        if row.id is None:
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
        result.derived.append(
            OwnedBlob(
                key=backend.thumbnail_key(row.id),
                resource_type="thumbnail",
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

    Derived artefacts (thumbnails, the STL cache) and readme/body images are
    absent because neither sweeper walks their prefixes — they live under
    ``thumb_dir`` locally and outside ``vault-data/files/`` on S3. Widening a
    walker to cover them means teaching this function to enumerate them first.
    """
    snapshot = ownership_snapshot(session, discover=False)
    # Compatibility contract: external File.path values historically appeared
    # here even though trash and backup callers separately avoid deleting them.
    return {blob.key for blob in (*snapshot.primary, *snapshot.external)}
