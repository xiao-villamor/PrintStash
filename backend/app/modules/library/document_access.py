"""Resolve the current Collection authority for a Document."""

from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import Document, Model
from app.db.scopes import live


def collection_id(session: Session, document: Document) -> int | None:
    if document.model_id is None:
        return document.collection_id
    owner = session.exec(
        select(col(Model.collection_id), col(Model.id)).where(
            Model.id == document.model_id, live(Model)
        )
    ).one_or_none()
    if owner is None:
        raise OperationError("model_not_found", kind=ErrorKind.NOT_FOUND)
    return owner[0]
