"""Row builders shared by every `/printers` endpoint group.

Printer RBAC is per printer rather than global, so almost every test in this folder needs
the same two things: a user at a chosen scope, and a role granted to that user on one
specific printer. A caller with rights on *a* printer and none on the one under test is
the shape most of the negative rows take.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import Printer, PrinterPermission, PrinterRole, User
from app.services.auth import create_access_token
from tests.factories import build_user


def user_headers(
    db_session: Session,
    username: str,
    *,
    is_superuser: bool = False,
    scope: str = "write",
) -> dict[str, str]:
    """Create a user and return bearer headers for it."""
    user = build_user(
        db_session,
        username=username,
        password="Password123",
        active=True,
        superuser=is_superuser,
    )
    token = create_access_token(user.id, user.username, scope=scope)
    return {"Authorization": f"Bearer {token}"}


def grant_printer(
    db_session: Session, username: str, printer: Printer, role: PrinterRole
) -> None:
    """Give an existing user a role on one printer."""
    user = db_session.exec(select(User).where(User.username == username)).one()
    db_session.add(PrinterPermission(user_id=user.id, printer_id=printer.id, role=role))
    db_session.commit()
