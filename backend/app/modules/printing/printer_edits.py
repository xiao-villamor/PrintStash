"""Atomic settings edits, isolated from live printer telemetry.

The caller owns commit/rollback. Legacy edits remain accepted, but advance the
same settings history; credentials stay in the printer row and never in tokens.
"""

import re

from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import LibraryRevision, Printer, PrinterRole, User
from app.db.scopes import live
from app.modules.identity import printer_rbac
from app.schemas.editing import EditContract, EditingBase, EditPrecondition

_MAX_EDIT_VERSION = str(2**63 - 1)


def expected_base(
    printer_id: int, precondition: EditPrecondition
) -> EditingBase | None:
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
    matched = re.fullmatch(
        r'"printer-([1-9][0-9]*)-e([0-9a-f]{32})-v([1-9][0-9]*)"', precondition.if_match
    )
    if (
        matched is None
        or matched[1] != str(printer_id)
        or len(matched[3]) > len(_MAX_EDIT_VERSION)
        or (
            len(matched[3]) == len(_MAX_EDIT_VERSION) and matched[3] > _MAX_EDIT_VERSION
        )
    ):
        raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
    return EditingBase(edit_epoch=matched[2], edit_version=int(matched[3]))


def claim(
    session: Session, actor: User, printer: Printer, precondition: EditPrecondition
) -> None:
    if printer.id is None:
        raise ValueError("printer_edit_requires_persisted_row")
    base = expected_base(printer.id, precondition)
    statement = update(Printer).where(col(Printer.id) == printer.id, live(Printer))
    if base is not None:
        statement = statement.where(
            col(Printer.edit_version) == base.edit_version,
            select(col(LibraryRevision.epoch))
            .where(col(LibraryRevision.id) == 1)
            .scalar_subquery()
            == base.edit_epoch,
        )
    with session.no_autoflush:
        claimed = session.execute(
            statement.values(edit_version=col(Printer.edit_version) + 1)
            .returning(col(Printer.edit_version))
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if claimed is None:
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        authority = session.execute(
            select(
                col(User.is_active), col(User.is_superuser), col(User.auth_version)
            ).where(col(User.id) == actor.id)
        ).one_or_none()
        if (
            authority is None
            or not authority.is_active
            or authority.auth_version != actor.auth_version
        ):
            raise OperationError("printer_permission_denied", kind=ErrorKind.FORBIDDEN)
        set_committed_value(actor, "is_superuser", authority.is_superuser)
        session.refresh(printer)
        printer_rbac.require_printer_role(session, actor, printer.id, PrinterRole.ADMIN)
