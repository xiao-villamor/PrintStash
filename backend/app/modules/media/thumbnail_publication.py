"""Publish one encoded thumbnail as an immutable object and point readers at it.

Each published thumbnail gets a new content-addressed key, so a re-derivation
never overwrites the bytes a reader may be serving: the Artifact's pointer (and
the Model's, when this Artifact represents it) switches only once the new
object is durable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlmodel import Session

from app.db.models import File, FileType, Model
from app.modules.storage.storage_backend.contracts import (
    StorageBackend,
    StorageCollisionError,
)
from app.modules.storage.storage_ownership import (
    adopt_publication,
    prepare_bytes,
    provider_ref_for_backend,
)
from app.modules.storage.storage_publication import (
    PendingPublication,
    VerifiedPublication,
)

MESH_TYPES = frozenset({FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP})


class ThumbnailPublicationError(RuntimeError):
    """A colliding object exists under the key and holds different bytes."""


@dataclass(frozen=True)
class PublishedThumbnail:
    key: str
    sha256: str
    size: int
    etag: str | None


@dataclass(frozen=True)
class PreparedThumbnail:
    published: PublishedThumbnail
    publication: PendingPublication | VerifiedPublication


def prepare_thumbnail(
    session: Session,
    backend: StorageBackend,
    file_row: File,
    encoded: bytes,
    *,
    recipe_tag: str,
) -> PreparedThumbnail:
    """Prepare ``encoded`` under a key unique to (Artifact, recipe, bytes)."""
    assert file_row.id is not None
    digest = hashlib.sha256(encoded).hexdigest()
    variant = hashlib.sha256(f"{recipe_tag}:{digest}".encode()).hexdigest()
    key = backend.thumbnail_variant_key(file_row.id, file_row.sha256, variant)
    try:
        candidate = prepare_bytes(
            session, backend, key, encoded, object_kind="thumbnail", sha256=digest
        )
        receipt = candidate.receipt
        return PreparedThumbnail(
            PublishedThumbnail(key, digest, receipt.size, receipt.etag), candidate
        )
    except StorageCollisionError as exc:
        existing = backend.object_info(key)
        if (
            existing is None
            or existing.size != len(encoded)
            or hashlib.sha256(backend.read_bytes(key)).hexdigest() != digest
        ):
            raise ThumbnailPublicationError("thumbnail_key_collision") from exc
        from dataclasses import replace

        receipt = backend.adopt_existing(
            key, expected_size=len(encoded), expected_sha256=digest
        )
        if receipt is None:
            raise ThumbnailPublicationError("thumbnail_ownership_unverified") from exc
        receipt = replace(
            receipt,
            provider_ref=provider_ref_for_backend(backend, namespace=receipt.namespace),
        )
        return PreparedThumbnail(
            PublishedThumbnail(key, digest, receipt.size, receipt.etag),
            VerifiedPublication(receipt, "thumbnail", digest),
        )


def adopt_thumbnail(
    session: Session, prepared: PreparedThumbnail
) -> PublishedThumbnail:
    """Join preparation evidence after output/authority locks; SQL only."""
    adopt_publication(session, prepared.publication)
    return prepared.published


def publish_thumbnail(
    session: Session,
    backend: StorageBackend,
    file_row: File,
    encoded: bytes,
    *,
    recipe_tag: str,
) -> PublishedThumbnail:
    prepared = prepare_thumbnail(
        session, backend, file_row, encoded, recipe_tag=recipe_tag
    )
    return adopt_thumbnail(session, prepared)


def should_represent_model(session: Session, model: Model, file_row: File) -> bool:
    """Whether ``file_row``'s thumbnail should become the Model's thumbnail.

    Deterministic whatever order derivations finish in: a Model shows its
    newest live mesh when it has one, and otherwise keeps the first thumbnail
    it got. A re-derivation of the current representative always refreshes it.
    """
    if model.thumbnail_file_id is None or model.thumbnail_file_id == file_row.id:
        return True
    current = session.get(File, model.thumbnail_file_id)
    if current is None or current.deleted_at is not None:
        return True
    if file_row.file_type not in MESH_TYPES:
        return False
    if current.file_type not in MESH_TYPES:
        return True
    return (file_row.id or 0) > (current.id or 0)


def point_at(session: Session, file_row: File, key: str) -> None:
    """Point the Artifact (and its Model, when it represents it) at ``key``."""
    file_row.thumbnail_path = key
    session.add(file_row)
    model = session.get(Model, file_row.model_id)
    if model is not None and should_represent_model(session, model, file_row):
        model.thumbnail_file_id = file_row.id
        model.thumbnail_path = key
        session.add(model)
