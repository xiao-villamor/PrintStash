"""Model-owned cover snapshots, independent from Artifact thumbnail regeneration."""

from __future__ import annotations

import hashlib
from uuid import uuid4

from sqlmodel import Session, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import (
    Model,
    ModelCover,
    ModelProvenanceSource,
    ModelSourceCover,
    User,
)
from app.modules.library.edit_preconditions import EditKind, claim, expected_base
from app.modules.media.source_cover_processing import (
    ProcessedSourceCover,
    SourceCoverProcessingError,
    process_source_cover_upload,
)
from app.modules.storage.storage_backend.contracts import StorageTier
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_deletion import (
    PreparedOwnedDeletion,
    enqueue_prepared_owned_deletion,
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


def _retirement(session: Session, cover: ModelCover) -> PreparedOwnedDeletion:
    backend = get_backend()
    try:
        prepared = prepare_owned_key_deletion(
            session,
            backend,
            backend.model_cover_key(cover.model_id, cover.filename),
            required_proof=True,
            resource_kind="model_cover",
            resource_id=cover.model_id,
            allow_unverified=backend.capabilities.tier is not StorageTier.VERIFIED,
        )
    except UnsafeStorageDeleteError as exc:
        raise OperationError(
            "storage_ownership_unverified", kind=ErrorKind.CONFLICT
        ) from exc
    if prepared is None:
        raise OperationError("storage_ownership_unverified", kind=ErrorKind.CONFLICT)
    return prepared


def content(model: Model) -> bytes:
    if model.id is None or model.cover_filename is None:
        raise OperationError("model_cover_not_found", kind=ErrorKind.NOT_FOUND)
    backend = get_backend()
    try:
        return backend.read_bytes(
            backend.model_cover_key(model.id, model.cover_filename)
        )
    except FileNotFoundError as exc:
        raise OperationError("model_cover_blob_missing", kind=ErrorKind.GONE) from exc


def put(
    session: Session,
    actor: User,
    model: Model,
    data: bytes,
    content_type: str | None,
    precondition: EditPrecondition,
) -> None:
    """Publish immutable bytes before conditionally adopting them into the Model."""
    if model.id is None:
        raise ValueError("model_cover_requires_persisted_model")
    expected_base(EditKind.MODEL, model.id, precondition)
    try:
        processed = process_source_cover_upload(data, content_type)
    except SourceCoverProcessingError as exc:
        raise OperationError(
            "model_cover_invalid", kind=ErrorKind.UNPROCESSABLE
        ) from exc
    _publish(session, actor, model, processed, precondition)


def _publish(
    session: Session,
    actor: User,
    model: Model,
    processed: ProcessedSourceCover,
    precondition: EditPrecondition,
) -> None:
    if model.id is None:
        raise ValueError("model_cover_requires_persisted_model")
    expected_base(EditKind.MODEL, model.id, precondition)
    # Independent publication reservations must not contend with a read snapshot.
    session.commit()
    digest = hashlib.sha256(processed.data).hexdigest()
    filename = f"{digest}-{uuid4().hex}.webp"
    backend = get_backend()
    candidate = prepare_bytes(
        session,
        backend,
        backend.model_cover_key(model.id, filename),
        processed.data,
        object_kind="model_cover",
        sha256=digest,
    )
    try:
        claim(session, actor, model, precondition)
        previous = session.get(ModelCover, model.id)
        retirement = _retirement(session, previous) if previous is not None else None
        if previous is None:
            cover = ModelCover(
                model_id=model.id,
                filename=filename,
                size_bytes=len(processed.data),
                sha256=digest,
            )
        else:
            cover = previous
            cover.filename = filename
            cover.size_bytes = len(processed.data)
            cover.sha256 = digest
        session.add(cover)
        model.updated_by = actor.id
        model.updated_at = utcnow()
        session.add(model)
        session.flush()
        finish_publication_batch(
            session,
            publications=(candidate,),
            retirements=(retirement,) if retirement is not None else (),
        )
        session.commit()
    except Exception:
        # Preserve publication evidence on uncertain commits; the ownership
        # reconciler checks committed references before reclaiming the candidate.
        session.rollback()
        abandon_publication(session, candidate)
        raise
    if retirement is not None:
        process_storage_delete_intents()
    session.refresh(model)


def select_source(
    session: Session,
    actor: User,
    model: Model,
    source_id: int,
    precondition: EditPrecondition,
) -> None:
    """Copy a source cover so future source edits cannot change the chosen image."""
    cover = session.exec(
        select(ModelSourceCover)
        .join(ModelProvenanceSource)
        .where(
            ModelProvenanceSource.id == source_id,
            ModelProvenanceSource.model_id == model.id,
        )
    ).first()
    if cover is None:
        raise OperationError("source_cover_not_found", kind=ErrorKind.NOT_FOUND)
    try:
        data = get_backend().read_bytes(cover.storage_key)
    except FileNotFoundError as exc:
        raise OperationError("source_cover_blob_missing", kind=ErrorKind.GONE) from exc
    # Source covers have already passed the canonical conversion seam. Copy
    # their exact normalized bytes instead of applying another lossy encoding.
    _publish(session, actor, model, ProcessedSourceCover(data=data), precondition)


def reset(
    session: Session, actor: User, model: Model, precondition: EditPrecondition
) -> None:
    claim(session, actor, model, precondition)
    cover = session.get(ModelCover, model.id)
    if cover is not None:
        retirement = _retirement(session, cover)
        session.delete(cover)
        session.flush()
        enqueue_prepared_owned_deletion(session, retirement, required_proof=True)
    model.updated_by = actor.id
    model.updated_at = utcnow()
    session.add(model)
    session.commit()
    if cover is not None:
        process_storage_delete_intents()
    session.refresh(model)
