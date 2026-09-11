"""Optional Model annotations supplied by application composition.

Library identity, Families, authorization and pagination do not depend on an
annotation provider or its tables. The installed application can add badges and
their matching SQL predicate without importing that feature from library code.
"""

from typing import Protocol

from sqlalchemy import false
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session

from app.db.models import User


class ModelAnnotations(Protocol):
    def summaries(
        self, session: Session, actor: User, model_ids: list[int]
    ) -> dict[int, dict[str, int]]: ...

    def has_open_candidates(
        self, session: Session, actor: User
    ) -> ColumnElement[bool]: ...


_annotations: ModelAnnotations | None = None


def bind_annotations(provider: ModelAnnotations | None) -> None:
    global _annotations
    _annotations = provider


def similarity_summaries(
    session: Session, actor: User, model_ids: list[int]
) -> dict[int, dict[str, int]]:
    return _annotations.summaries(session, actor, model_ids) if _annotations else {}


def has_open_candidates(session: Session, actor: User) -> ColumnElement[bool]:
    return _annotations.has_open_candidates(session, actor) if _annotations else false()
