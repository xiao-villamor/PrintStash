"""Optional Model annotations supplied by application composition.

Library identity, Families, authorization and pagination do not depend on an
annotation provider or its tables. The installed application can add badges and
their matching SQL predicate without importing that feature from library code.
"""

from typing import Protocol

from sqlalchemy import false
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.db.models import User
from app.schemas.models import ModelFamilyRead


class FamilyAnnotations(Protocol):
    def family_summaries(
        self, session: Session, user: User, model_ids: list[int]
    ) -> dict[int, ModelFamilyRead]: ...

    def membership_rows(self, session: Session, user: User): ...


class ModelAnnotations(Protocol):
    def summaries(
        self, session: Session, actor: User, model_ids: list[int]
    ) -> dict[int, dict[str, int]]: ...

    def has_open_candidates(
        self, session: Session, actor: User
    ) -> ColumnElement[bool]: ...


_annotations: ModelAnnotations | None = None
_families: FamilyAnnotations | None = None


def bind_families(provider: FamilyAnnotations | None) -> None:
    global _families
    _families = provider


def family_summaries(
    session: Session, actor: User, model_ids: list[int]
) -> dict[int, ModelFamilyRead]:
    return _families.family_summaries(session, actor, model_ids) if _families else {}


def membership_rows(session: Session, actor: User):
    if _families is None:
        raise OperationError("family_unavailable", kind=ErrorKind.UNAVAILABLE)
    return _families.membership_rows(session, actor)


def bind_annotations(provider: ModelAnnotations | None) -> None:
    global _annotations
    _annotations = provider


def similarity_summaries(
    session: Session, actor: User, model_ids: list[int]
) -> dict[int, dict[str, int]]:
    return _annotations.summaries(session, actor, model_ids) if _annotations else {}


def has_open_candidates(session: Session, actor: User) -> ColumnElement[bool]:
    return _annotations.has_open_candidates(session, actor) if _annotations else false()
