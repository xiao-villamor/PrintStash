"""Per-printer role resolution and enforcement."""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.db.models import Printer, PrinterPermission, PrinterRole, User
from app.db.scopes import live

ROLE_ORDER = {
    PrinterRole.VIEW: 1,
    PrinterRole.PRINT: 2,
    PrinterRole.CONTROL: 3,
    PrinterRole.ADMIN: 4,
}


def role_allows(role: PrinterRole | None, minimum: PrinterRole) -> bool:
    return role is not None and ROLE_ORDER[role] >= ROLE_ORDER[minimum]


def effective_printer_role(
    session: Session, user: User, printer_id: int
) -> PrinterRole | None:
    if user.is_superuser:
        return PrinterRole.ADMIN
    return session.exec(
        select(PrinterPermission.role)
        .join(Printer, Printer.id == PrinterPermission.printer_id)  # type: ignore[arg-type]
        .where(
            PrinterPermission.user_id == user.id,
            PrinterPermission.printer_id == printer_id,
            live(Printer),
        )
    ).first()


def effective_roles_for_printers(
    session: Session, user: User, printer_ids: Iterable[int]
) -> dict[int, PrinterRole | None]:
    ids = {int(printer_id) for printer_id in printer_ids}
    if user.is_superuser:
        return {printer_id: PrinterRole.ADMIN for printer_id in ids}
    roles = {printer_id: None for printer_id in ids}
    if not ids:
        return roles
    for printer_id, role in session.exec(
        select(PrinterPermission.printer_id, PrinterPermission.role)
        .join(Printer, Printer.id == PrinterPermission.printer_id)  # type: ignore[arg-type]
        .where(
            PrinterPermission.user_id == user.id,
            PrinterPermission.printer_id.in_(ids),  # type: ignore[union-attr]
            live(Printer),
        )
    ).all():
        roles[int(printer_id)] = role
    return roles


def effective_roles_for_user_printer_pairs(
    session: Session,
    user_ids: Iterable[int],
    printer_ids: Iterable[int],
) -> dict[tuple[int, int], PrinterRole]:
    """Load direct printer grants for many scheduler candidates in one query."""
    users = {int(user_id) for user_id in user_ids}
    printers = {int(printer_id) for printer_id in printer_ids}
    if not users or not printers:
        return {}
    return {
        (int(user_id), int(printer_id)): role
        for user_id, printer_id, role in session.exec(
            select(
                PrinterPermission.user_id,
                PrinterPermission.printer_id,
                PrinterPermission.role,
            )
            .join(Printer, Printer.id == PrinterPermission.printer_id)  # type: ignore[arg-type]
            .where(
                PrinterPermission.user_id.in_(users),  # type: ignore[union-attr]
                PrinterPermission.printer_id.in_(printers),  # type: ignore[union-attr]
                live(Printer),
            )
        ).all()
    }


def accessible_printer_ids(
    session: Session,
    user: User,
    minimum: PrinterRole = PrinterRole.VIEW,
) -> set[int]:
    if user.is_superuser:
        rows = session.exec(select(Printer.id).where(live(Printer))).all()
    else:
        allowed_roles = [role for role in PrinterRole if role_allows(role, minimum)]
        rows = session.exec(
            select(PrinterPermission.printer_id)
            .join(Printer, Printer.id == PrinterPermission.printer_id)  # type: ignore[arg-type]
            .where(
                PrinterPermission.user_id == user.id,
                PrinterPermission.role.in_(allowed_roles),  # type: ignore[union-attr]
                live(Printer),
            )
        ).all()
    return {int(printer_id) for printer_id in rows if printer_id is not None}


def require_printer_role(
    session: Session,
    user: User,
    printer_id: int,
    minimum: PrinterRole,
) -> PrinterRole:
    printer = session.exec(
        select(Printer).where(Printer.id == printer_id, live(Printer))
    ).first()
    if printer is None:
        raise HTTPException(status_code=404, detail="printer_not_found")
    role = effective_printer_role(session, user, printer_id)
    if role_allows(role, minimum):
        return role  # type: ignore[return-value]
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="printer_permission_denied",
    )
