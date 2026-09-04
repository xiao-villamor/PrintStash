"""Whether a printer is inside a maintenance window right now.

Routing consults this before every dispatch, so the answer for a printer with no
windows at all has to be a plain `False`. That sounds too obvious to test, and it
is the reason it is here: the query joins a table that is empty on most
installations, and an implementation that returned a row-or-`None` without
coercing would make every dispatch decision on a `None` that is falsy by accident
rather than by contract.
"""

from __future__ import annotations

from sqlmodel import Session

from app.db.models import Printer
from app.services import fleet


class TestActiveMaintenance:
    def test_reports_no_maintenance_for_a_printer_with_no_windows(
        self, db_session: Session, printer: Printer
    ) -> None:
        assert fleet._active_maintenance(db_session, int(printer.id)) is False
