"""Canonical Multipart cover publication, shared by edits and reviewed intake."""

import hashlib
from collections.abc import Callable
from uuid import uuid4

from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import MultipartModel, User
from app.modules.library.edit_preconditions import claim as claim_edit
from app.modules.media.source_cover_processing import ProcessedSourceCover
from app.modules.storage.storage_backend.contracts import StorageTier
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_deletion import (
    PreparedOwnedDeletion,
    prepare_owned_key_deletion,
    process_storage_delete_intents,
)
from app.modules.storage.storage_ownership import (
    UnsafeStorageDeleteError,
    abandon_publication,
    finish_publication_batch,
    prepare_bytes,
)
from app.schemas.editing import EditPrecondition


def uploaded_key(aggregate: MultipartModel) -> str | None:
    if aggregate.id is None or aggregate.cover_filename is None:
        return None
    return get_backend().multipart_model_cover_key(
        int(aggregate.id), aggregate.cover_filename
    )


def prepare_delete(
    session: Session,
    aggregate: MultipartModel,
    *,
    key: str | None = None,
) -> PreparedOwnedDeletion | None:
    key = key or uploaded_key(aggregate)
    if key is None:
        return None
    backend = get_backend()
    try:
        prepared = prepare_owned_key_deletion(
            session,
            backend,
            key,
            required_proof=True,
            resource_kind="multipart_model_cover",
            resource_id=aggregate.id,
            # A Guarded/NAS root can retire the catalog reference using the
            # committed content hash. Its outbox intent retains the bytes as
            # blocked; this never authorizes an unverified physical deletion.
            allow_unverified=backend.capabilities.tier is not StorageTier.VERIFIED,
        )
    except UnsafeStorageDeleteError as exc:
        raise OperationError(
            "storage_ownership_unverified", kind=ErrorKind.CONFLICT
        ) from exc
    aggregate.cover_filename = None
    aggregate.cover_content_type = None
    aggregate.cover_size_bytes = None
    return prepared


def publish(
    session: Session,
    user: User,
    aggregate: MultipartModel,
    processed: ProcessedSourceCover,
    precondition: EditPrecondition,
    *,
    before_commit: Callable[[Session], None] | None = None,
) -> None:
    """Adopt the cover and the caller's fenced receipt in one transaction."""
    assert aggregate.id is not None
    # The ownership ledger reserves through an independent session. Release
    # this read-only transaction first so SQLite never has two writers waiting
    # on the same connection while the cover publication becomes durable.
    session.commit()
    digest = hashlib.sha256(processed.data).hexdigest()
    filename = f"{digest[:16]}-{uuid4().hex}.webp"
    backend = get_backend()
    key = backend.multipart_model_cover_key(aggregate.id, filename)
    candidate = None
    try:
        candidate = prepare_bytes(
            session,
            backend,
            key,
            processed.data,
            object_kind="multipart_model_cover",
            sha256=digest,
        )
        claim_edit(session, user, aggregate, precondition)
        old_cover_key = uploaded_key(aggregate)
        retirement = (
            prepare_delete(session, aggregate, key=old_cover_key)
            if old_cover_key is not None
            else None
        )
        aggregate.cover_filename = filename
        aggregate.cover_content_type = processed.content_type
        aggregate.cover_size_bytes = len(processed.data)
        aggregate.cover_image_url = None
        aggregate.updated_by = user.id
        aggregate.updated_at = utcnow()
        session.add(aggregate)
        session.flush()
        finish_publication_batch(
            session,
            publications=(candidate,),
            retirements=(retirement,) if retirement is not None else (),
        )
        if before_commit is not None:
            before_commit(session)
        session.commit()
    except BaseException:
        session.rollback()
        if candidate is not None:
            abandon_publication(session, candidate)
        raise
    if old_cover_key is not None:
        process_storage_delete_intents()
    session.refresh(aggregate)
