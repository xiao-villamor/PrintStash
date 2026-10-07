"""Atomic metadata edit claims with an explicit additive compatibility contract.

Version comparison and advancement are one database UPDATE. The caller owns
commit/rollback so validation errors cannot leave a consumed version behind.
Database triggers additionally advance metadata versions for legacy/bulk paths.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    CollectionRole,
    Document,
    LibraryRevision,
    Model,
    MultipartModel,
    User,
)
from app.db.scopes import live
from app.modules.identity import rbac
from app.schemas.editing import EditingBase


class EditKind(str, Enum):
    MODEL = "model"
    MULTIPART = "multipart"
    DOCUMENT = "document"


class EditContract(str, Enum):
    CONDITIONAL_V1 = "conditional-v1"


class EditPrecondition(BaseModel):
    if_match: str | None = None
    contract: str | None = None


def etag(kind: EditKind, id: int, version: int, epoch: str) -> str:
    return f'"{kind.value}-{id}-e{epoch}-v{version}"'


def expected_base(
    kind: EditKind, id: int, precondition: EditPrecondition
) -> EditingBase | None:
    """Validate headers without claiming a write, before preparing stored bytes."""
    if (
        precondition.contract is not None
        and precondition.contract != EditContract.CONDITIONAL_V1.value
    ):
        raise OperationError("edit_contract_invalid")
    if precondition.contract is not None and precondition.if_match is None:
        raise OperationError(
            "edit_precondition_required", kind=ErrorKind.PRECONDITION_REQUIRED
        )
    expected: EditingBase | None = None
    if precondition.if_match is not None:
        match = re.fullmatch(
            r'"(model|multipart|document)-([1-9][0-9]*)-e([0-9a-f]{32})-v([1-9][0-9]*)"',
            precondition.if_match,
        )
        if match is None or match[1] != kind.value or int(match[2]) != id:
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        expected = EditingBase(edit_version=int(match[4]), edit_epoch=match[3])
    return expected


def claim(
    session: Session,
    actor: User,
    row: Model | MultipartModel | Document,
    precondition: EditPrecondition,
) -> None:
    entity = type(row)
    kind = {
        Model: EditKind.MODEL,
        MultipartModel: EditKind.MULTIPART,
        Document: EditKind.DOCUMENT,
    }[entity]
    if row.id is None:
        raise ValueError("edit_claim_requires_persisted_aggregate")
    expected = expected_base(kind, row.id, precondition)
    statement = update(entity).where(col(entity.id) == row.id)
    if entity is not MultipartModel:
        statement = statement.where(live(entity))
    if expected is not None:
        statement = statement.where(
            col(entity.edit_version) == expected.edit_version,
            select(col(LibraryRevision.epoch))
            .where(col(LibraryRevision.id) == 1)
            .scalar_subquery()
            == expected.edit_epoch,
        )
    claimed = session.execute(
        statement.values(edit_version=col(entity.edit_version) + 1)
        .returning(col(entity.edit_version))
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if claimed is None:
        raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
    # The authenticated actor may predate a committed account/session change.
    # Scalar reads bypass the identity map inside the catalog write window.
    authority = session.execute(
        select(
            col(User.auth_version), col(User.is_active), col(User.is_superuser)
        ).where(col(User.id) == actor.id)
    ).one_or_none()
    if (
        authority is None
        or not authority.is_active
        or authority.auth_version != actor.auth_version
    ):
        raise OperationError("collection_permission_denied", kind=ErrorKind.FORBIDDEN)
    set_committed_value(actor, "is_superuser", authority.is_superuser)
    session.refresh(row)
    set_committed_value(row, "edit_version", claimed)
    # A concurrent move can have changed the collection between initial lookup
    # and the atomic UPDATE; authorize the row we actually locked/refreshed.
    rbac.require_collection_role(session, actor, row.collection_id, CollectionRole.EDIT)


def validate_batch(
    contract: str | None, ids: list[int], versions: dict[int, EditingBase] | None
) -> None:
    """A versioned selection supplies exactly the set of Models it will edit."""
    if contract is not None and contract != EditContract.CONDITIONAL_V1.value:
        raise OperationError("edit_contract_invalid")
    if versions is None:
        if contract is not None:
            raise OperationError(
                "edit_precondition_required", kind=ErrorKind.PRECONDITION_REQUIRED
            )
        return
    if set(versions) - set(ids):
        raise OperationError("edit_precondition_invalid")
    if set(ids) - set(versions):
        raise OperationError(
            "edit_precondition_required", kind=ErrorKind.PRECONDITION_REQUIRED
        )
