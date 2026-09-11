"""Publish independent Family covers with exact ownership and compensating cleanup."""

import hashlib
from dataclasses import dataclass
from uuid import uuid4

from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.db.models import ModelFamily, User
from app.modules.media.source_cover_processing import (
    SourceCoverProcessingError,
    process_source_cover_upload,
)
from app.modules.storage.storage_backend.contracts import CreationReceipt
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_deletion import enqueue_owned_key
from app.modules.storage.storage_ownership import publish_bytes

from .access import lock_families, require
from .mutations import record, touch


@dataclass(frozen=True)
class PreparedCover:
    filename: str
    sha256: str
    receipt: CreationReceipt


def prepare_upload(
    session: Session, export_id: str, data: bytes, content_type: str | None
) -> PreparedCover:
    """Publish before a Family transaction using its durable portable identity.

    A new imported Family has no database ID yet. The export UUID lets it use
    the same owner and durable publication intent as an existing Family, while
    its metadata and all memberships can still commit atomically.
    """
    try:
        processed = process_source_cover_upload(data, content_type)
    except SourceCoverProcessingError as exc:
        raise OperationError(
            "family_cover_invalid", kind=ErrorKind.UNPROCESSABLE
        ) from exc
    digest = hashlib.sha256(processed.data).hexdigest()
    filename = f"{digest[:16]}-{uuid4().hex}.webp"
    backend = get_backend()
    receipt = publish_bytes(
        session,
        backend,
        backend.model_family_cover_key(export_id, filename),
        processed.data,
        object_kind="model_family_cover",
        sha256=digest,
    )
    return PreparedCover(filename, digest, receipt)


def attach_upload(
    session: Session, family: ModelFamily, prepared: PreparedCover
) -> None:
    clear_upload(session, family)
    family.cover_filename = prepared.filename
    family.cover_content_type = "image/webp"
    family.cover_size_bytes = prepared.receipt.size
    family.cover_image_url = None
    family.cover_model_id = None
    session.add(family)


def uploaded_key(family: ModelFamily) -> str | None:
    if family.id is None or family.cover_filename is None:
        return None
    return get_backend().model_family_cover_key(family.export_id, family.cover_filename)


def clear_upload(session: Session, family: ModelFamily) -> None:
    """Queue exact owned bytes for deletion in the caller's domain transaction."""
    key = uploaded_key(family)
    if key is not None:
        enqueue_owned_key(
            session,
            get_backend(),
            key,
            required_proof=True,
            resource_kind="model_family_cover",
            resource_id=family.id,
        )
    family.cover_filename = None
    family.cover_content_type = None
    family.cover_size_bytes = None
    session.add(family)


def upload(
    session: Session,
    user: User,
    family_id: int,
    version: int,
    data: bytes,
    content_type: str | None,
) -> ModelFamily:
    """Own the publication transaction, including compensation if commit fails.

    The independent ownership reservation requires a clean transaction. Release
    the first permission read, then recheck all reserved members and the version
    after publication; a concurrent edit cannot be overwritten by this upload.
    """
    family = require(session, user, family_id, edit=True)
    if family.version != version:
        raise OperationError("family_revision_conflict", kind=ErrorKind.CONFLICT)
    export_id = family.export_id
    session.commit()
    backend = get_backend()
    prepared: PreparedCover | None = None
    try:
        prepared = prepare_upload(session, export_id, data, content_type)
        lock_families(session, [family_id])
        family = require(session, user, family_id, edit=True)
        touch(session, user, family, version)
        attach_upload(session, family, prepared)
        record(session, user, family, "cover_upload", {"sha256": prepared.sha256})
        session.commit()
    except Exception:
        session.rollback()
        if prepared is not None:
            backend.rollback_create(prepared.receipt)
        raise
    return family


def remove(session: Session, user: User, family_id: int, version: int) -> ModelFamily:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True)
    touch(session, user, family, version)
    clear_upload(session, family)
    family.cover_image_url = None
    family.cover_model_id = None
    record(session, user, family, "cover_remove", {})
    return family
