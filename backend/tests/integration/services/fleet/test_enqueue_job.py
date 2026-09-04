"""Queueing one job, and refusing to start a print that will fail.

`enqueue_job` is where the material check becomes a decision with a consequence.
A proven mismatch does not queue: the loaded filament is not the one the artifact
asks for, and starting it wastes a spool and can damage a nozzle. So it raises
`material_mismatch_confirmation_required` — a *confirmation* rather than a flat
refusal, because the operator may know something the state does not and can
re-send with the mismatch accepted.

The distinction from `unknown` is the point. Only a proven mismatch stops here;
an unproven printer queues normally, or the feature would block most of a fleet
that cannot report its spools.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import File, Printer, User
from app.schemas.fleet import QueueJobCreate
from app.services import fleet
from tests.integration.services.fleet.conftest import load_manual_state


class TestEnqueueJob:
    def test_queues_a_job_on_a_printer_whose_state_is_unproven(
        self, db_session: Session, operator: User, printer: Printer, pla_artifact: File
    ) -> None:
        job = fleet.enqueue_job(
            db_session,
            QueueJobCreate(
                file_id=int(pla_artifact.id),
                strategy="manual",
                printer_id=int(printer.id),
            ),
            operator,
        )

        # Nothing is known about this printer's filament. Refusing here would
        # block most of a real fleet.
        assert job.printer_id == printer.id

    def test_refuses_a_known_mismatch_until_the_operator_confirms(
        self, db_session: Session, operator: User, printer: Printer, pla_artifact: File
    ) -> None:
        load_manual_state(db_session, printer, operator, material="ABS")

        with pytest.raises(fleet.FleetError) as error:
            fleet.enqueue_job(
                db_session,
                QueueJobCreate(
                    file_id=int(pla_artifact.id),
                    strategy="manual",
                    printer_id=int(printer.id),
                ),
                operator,
            )

        # A confirmation rather than a flat refusal: the operator may know
        # something the recorded state does not, and can re-send accepting it.
        assert error.value.code == "material_mismatch_confirmation_required"
