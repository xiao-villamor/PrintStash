"""Publish document uploads through the ownership ledger, with optional durable intent.

An ingestion caller records each document id in its own fenced transaction.
Replaying that intent reuses a completed document; an interrupted binary intent
is replaced before publication, so an unadopted blob is never overwritten.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO, Protocol

from sqlmodel import Session

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import CollectionRole, Document, DocumentKind, MultipartModel, User
from app.db.projections import content_changed
from app.modules.identity import rbac
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import (
    abandon_publication,
    adopt_publication,
    prepare_stream,
)


class UploadIntent(Protocol):
    """Caller-owned identity plus a fence checked at every domain commit."""

    document_id: int | None

    def record(self, session: Session, document_id: int) -> None: ...


def kind_for(filename: str) -> DocumentKind:
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return DocumentKind.MARKDOWN
    if suffix == ".pdf":
        return DocumentKind.PDF
    return DocumentKind.OTHER


def safe_filename(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", stem or base)[:120] or "document"
    return f"{safe}{dot}{ext.lower()}"


def publish(
    session: Session,
    user: User,
    *,
    stream: BinaryIO,
    filename: str,
    size: int | None,
    name: str,
    collection_id: int | None,
    aggregate: MultipartModel | None = None,
    intent: UploadIntent | None = None,
) -> Document:
    """Create or recover one upload; original stream ownership remains with caller."""
    rbac.require_collection_role(session, user, collection_id, CollectionRole.EDIT)
    if aggregate is not None and aggregate.collection_id != collection_id:
        raise OperationError("multipart_guide_collection_mismatch")
    if size is not None and size > settings.max_upload_bytes:
        raise OperationError("upload_too_large", kind=ErrorKind.TOO_LARGE)
    if not name.strip() or len(name) > 255:
        raise OperationError("document_name_invalid")

    previous = None
    if intent is not None and intent.document_id is not None:
        previous = session.get(Document, intent.document_id)
        if previous is None or previous.deleted_at is not None:
            raise OperationError(
                "document_import_result_missing", kind=ErrorKind.CONFLICT
            )
        if previous.created_by != user.id or previous.collection_id != collection_id:
            raise OperationError(
                "document_import_intent_mismatch", kind=ErrorKind.CONFLICT
            )
        if previous.kind is DocumentKind.MARKDOWN or previous.filename is not None:
            assert previous.id is not None
            intent.record(session, previous.id)
            session.commit()
            return previous

    doc = Document(
        name=name.strip(),
        kind=kind_for(filename),
        collection_id=collection_id,
        multipart_model_id=aggregate.id if aggregate is not None else None,
        created_by=user.id,
        updated_by=user.id,
    )

    def commit() -> None:
        session.flush()
        assert doc.id is not None
        if intent is not None:
            intent.record(session, doc.id)
        content_changed(session, "document", [doc.id])
        if aggregate is not None:
            aggregate.updated_at = utcnow()
            aggregate.updated_by = user.id
            session.add(aggregate)
        session.commit()

    if doc.kind is DocumentKind.MARKDOWN:
        body = stream.read(settings.max_upload_bytes + 1)
        if len(body) > settings.max_upload_bytes:
            raise OperationError("upload_too_large", kind=ErrorKind.TOO_LARGE)
        doc.body = body.decode("utf-8", errors="replace")
    try:
        session.add(doc)
        # Allocate the new id while an interrupted intent still exists. SQLite may
        # reuse its highest id after deletion, which would collide with its blob.
        session.flush()
        if previous is not None:
            content_changed(session, "document", [previous.id])
            session.delete(previous)
        commit()
    except BaseException:
        session.rollback()
        raise
    session.refresh(doc)
    if doc.kind is DocumentKind.MARKDOWN:
        return doc

    document_id = doc.id
    assert document_id is not None
    backend = get_backend()
    safe = safe_filename(filename)
    candidate = None
    try:
        candidate = prepare_stream(
            session,
            backend,
            backend.document_file_key(document_id, safe),
            stream,
            object_kind="document_file",
            expected_size=size,
            sha256=None,
        )
        if candidate.receipt.size > settings.max_upload_bytes:
            raise OperationError("upload_too_large", kind=ErrorKind.TOO_LARGE)
        doc.filename = safe
        doc.size_bytes = candidate.receipt.size
        session.add(doc)
        adopt_publication(session, candidate)
        commit()
    except BaseException as exc:  # cancellation also withdraws unpublished bytes
        session.rollback()
        if candidate is not None:
            if not abandon_publication(session, candidate):
                # A lost commit acknowledgement cannot delete an adopted result.
                raise
            backend.rollback_create(candidate.receipt)
        if intent is None:
            persisted = session.get(Document, document_id)
            if persisted is not None:
                session.delete(persisted)
                content_changed(session, "document", [document_id])
                session.commit()
        if isinstance(exc, StorageCollisionError):
            raise OperationError(
                "storage_destination_exists", kind=ErrorKind.CONFLICT
            ) from exc
        raise
    session.refresh(doc)
    return doc
