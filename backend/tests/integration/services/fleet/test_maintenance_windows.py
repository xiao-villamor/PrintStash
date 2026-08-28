"""Booking a printer out for servicing, and refusing to book out a ghost.

A maintenance window is how an operator says "not this machine, not today"
without deleting the printer or losing its queue. Routing reads the windows, so
every one of these endpoints resolves the printer first — and a window attached
to a printer that does not exist, or that was trashed, is a window nothing will
ever consult. It would sit in the list looking like protection while jobs kept
landing on whatever hardware the operator actually meant.

`_printer_or_error` is that resolution step, shared by the window and log
endpoints so the four of them cannot disagree about what a missing printer is.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import Printer, User
from app.schemas.fleet import MaintenanceWindowCreate
from app.services import fleet


class TestCreateMaintenanceWindow:
    def test_books_the_printer_out_for_the_requested_window(
        self, db_session: Session, operator: User, printer: Printer
    ) -> None:
        starts = utcnow()

        window = fleet.create_maintenance_window(
            db_session,
            int(printer.id),
            MaintenanceWindowCreate(
                starts_at=starts, ends_at=starts + timedelta(hours=2)
            ),
            operator,
        )

        assert window.printer_id == printer.id

    def test_reports_a_printer_that_does_not_exist(
        self, db_session: Session, operator: User
    ) -> None:
        starts = utcnow()

        # A window on no printer is protection nothing consults, and it would
        # still show in the list as though the machine were covered.
        with pytest.raises(fleet.FleetError, match="printer_not_found"):
            fleet.create_maintenance_window(
                db_session,
                999_999,
                MaintenanceWindowCreate(
                    starts_at=starts, ends_at=starts + timedelta(hours=2)
                ),
                operator,
            )

    def test_reports_a_printer_that_was_trashed(
        self, db_session: Session, operator: User
    ) -> None:
        from tests.factories import build_printer

        gone = build_printer(db_session, "Retired", trashed=True)
        starts = utcnow()

        with pytest.raises(fleet.FleetError, match="printer_not_found"):
            fleet.create_maintenance_window(
                db_session,
                int(gone.id),
                MaintenanceWindowCreate(
                    starts_at=starts, ends_at=starts + timedelta(hours=2)
                ),
                operator,
            )
