"""Conditional connection edits preserve identity, credentials and live authority.

The claim and acknowledged snapshot belong to the caller's write transaction.
"""

import hashlib
import re

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import StorageConnection, User
from app.schemas.editing import EditContract, EditingBase, EditPrecondition


def connection_base(connection: StorageConnection) -> EditingBase:
    if connection.id is None:
        raise ValueError("storage_connection_edit_requires_persisted_connection")
    identity = f"{connection.database_epoch}:storage-connection:{connection.id}:{connection.edit_identity}"
    return EditingBase(
        edit_epoch=hashlib.blake2b(identity.encode(), digest_size=16).hexdigest(),
        edit_version=connection.edit_version,
    )


def expected_base(aggregate: str, precondition: EditPrecondition) -> EditingBase | None:
    if (
        precondition.contract is not None
        and precondition.contract != EditContract.CONDITIONAL_V1.value
    ):
        raise OperationError("edit_contract_invalid")
    if precondition.if_match is None:
        if precondition.contract is not None:
            raise OperationError(
                "edit_precondition_required", kind=ErrorKind.PRECONDITION_REQUIRED
            )
        return None
    match = re.fullmatch(
        r'"' + re.escape(aggregate) + r'-e([0-9a-f]{32})-v([1-9][0-9]*)"',
        precondition.if_match,
    )
    maximum = str(2**63 - 1)
    if (
        match is None
        or len(match[2]) > len(maximum)
        or (len(match[2]) == len(maximum) and match[2] > maximum)
    ):
        raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
    return EditingBase(edit_epoch=match[1], edit_version=int(match[2]))


def _require_authority(session: Session, actor: User) -> None:
    authority = session.execute(
        select(
            col(User.is_active),
            col(User.is_superuser),
            col(User.auth_version),
            col(User.deleted_at),
        ).where(col(User.id) == actor.id)
    ).one_or_none()
    if (
        authority is None
        or not authority.is_active
        or not authority.is_superuser
        or authority.deleted_at is not None
        or authority.auth_version != actor.auth_version
    ):
        raise OperationError(
            "storage_connection_permission_denied", kind=ErrorKind.FORBIDDEN
        )


def claim_connection(
    session: Session,
    actor: User,
    connection: StorageConnection,
    precondition: EditPrecondition,
) -> None:
    if connection.id is None:
        raise ValueError("storage_connection_edit_requires_persisted_connection")
    base = expected_base(f"storage-connection-{connection.id}", precondition)
    statement = update(StorageConnection).where(
        col(StorageConnection.id) == connection.id
    )
    if base is not None:
        statement = statement.where(
            col(StorageConnection.edit_version) == base.edit_version
        )
    with session.no_autoflush:
        claimed = session.execute(
            statement.values(edit_version=col(StorageConnection.edit_version) + 1)
            .returning(col(StorageConnection.id))
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if claimed is None:
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        session.refresh(connection)
        if (
            base is not None
            and connection_base(connection).edit_epoch != base.edit_epoch
        ):
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        _require_authority(session, actor)
