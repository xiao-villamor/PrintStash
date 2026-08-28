"""Marking a printer as the default, or taking it out of rotation.

Two settings, two different reasons to be careful.

`is_default` is exclusive: exactly one printer can be the fallback for a job
that names none. The service clears the previous default before setting the new
one, and a unique constraint backs that up — so a race that got two through
surfaces as a named conflict rather than a database that quietly holds two
defaults and a router that picks whichever row it read first.

`drain_mode` is the soft off switch: the printer keeps its queue and its history
but stops receiving new work. That is what an operator reaches for before
servicing a machine, so it must not require deleting the printer — and the reason
travels with it, because a fleet view full of stopped machines with no
explanation is unusable.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.db.models import Printer, User
from app.schemas.fleet import PrinterRoutingUpdate
from app.services import fleet


class TestUpdateRouting:
    def test_makes_the_printer_the_default(
        self, db_session: Session, operator: User, printer: Printer
    ) -> None:
        updated = fleet.update_routing(
            db_session,
            int(printer.id),
            PrinterRoutingUpdate(is_default=True),
            operator,
        )

        assert updated.is_default is True

    def test_stops_the_printer_taking_new_work_with_a_reason(
        self, db_session: Session, operator: User, printer: Printer
    ) -> None:
        updated = fleet.update_routing(
            db_session,
            int(printer.id),
            PrinterRoutingUpdate(drain_mode=True, drain_reason="nozzle change"),
            operator,
        )

        # The reason is what makes a stopped machine legible in the fleet view.
        assert updated.drain_mode is True
        assert updated.drain_reason == "nozzle change"

    def test_reports_a_printer_that_does_not_exist(
        self, db_session: Session, operator: User
    ) -> None:
        with pytest.raises(fleet.FleetError, match="printer_not_found"):
            fleet.update_routing(
                db_session, 999_999, PrinterRoutingUpdate(drain_mode=True), operator
            )

    def test_reports_a_race_that_would_leave_two_defaults(
        self,
        db_session: Session,
        operator: User,
        printer: Printer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def conflict() -> None:
            raise IntegrityError("uq_printer_default", None, Exception("conflict"))

        monkeypatch.setattr(db_session, "commit", conflict)

        # Two concurrent requests both clearing the old default and setting a new
        # one. The constraint is the real guard; this is the error the loser gets
        # instead of a database holding two defaults for the router to choose
        # between at random.
        with pytest.raises(fleet.FleetError, match="default_printer_conflict"):
            fleet.update_routing(
                db_session,
                int(printer.id),
                PrinterRoutingUpdate(is_default=True),
                operator,
            )
