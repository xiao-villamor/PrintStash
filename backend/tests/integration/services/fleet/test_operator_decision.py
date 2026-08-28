"""What happens after a print finishes on a printer somebody has to attend to.

Some printers need a human between jobs — a part removed, a bed cleared. Those
are marked `operator_release_required`, and their finished jobs sit at a gate
until somebody answers. There are two answers and they mean opposite things.

**Release** says the machine is ready, and the queue moves on. **Hold** says it
is not — and the important half is that a hold also puts the printer into drain
mode. Without that, the queue would immediately dispatch the next job onto the
machine the operator just said was not ready, which is the exact failure the gate
exists to prevent. The drain reason names the job so the operator can see from
the fleet view why a machine stopped taking work.

A decision is answerable once. Answering twice is either a double-click or two
operators disagreeing, and silently letting the second answer win would undo a
hold somebody made deliberately.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import (
    File,
    OperatorGateState,
    Printer,
    PrinterStatus,
    PrintJobState,
    User,
)
from app.services import fleet
from tests.factories import build_print_job, build_printer


@pytest.fixture
def gated_printer(db_session: Session) -> Printer:
    return build_printer(
        db_session,
        name="Release gate",
        moonraker_url="http://release-gate",
        status=PrinterStatus.READY,
        operator_release_required=True,
    )


@pytest.fixture
def pending_job(db_session: Session, gated_printer: Printer, artifact: File):
    return build_print_job(
        db_session,
        artifact,
        printer=gated_printer,
        remote_filename="gate.gcode",
        state=PrintJobState.COMPLETED,
        operator_gate_state=OperatorGateState.PENDING,
    )


class TestOperatorDecision:
    def test_releases_the_gate_when_the_operator_says_the_machine_is_ready(
        self, db_session: Session, operator: User, pending_job
    ) -> None:
        decided = fleet.operator_decision(
            db_session, int(pending_job.id), "release", operator
        )

        assert decided.operator_gate_state == OperatorGateState.RELEASED

    def test_holds_the_gate_when_the_operator_says_it_is_not(
        self, db_session: Session, operator: User, pending_job
    ) -> None:
        decided = fleet.operator_decision(
            db_session, int(pending_job.id), "hold", operator
        )

        assert decided.operator_gate_state == OperatorGateState.HELD

    def test_stops_the_held_printer_taking_more_work(
        self,
        db_session: Session,
        operator: User,
        gated_printer: Printer,
        pending_job,
    ) -> None:
        fleet.operator_decision(db_session, int(pending_job.id), "hold", operator)

        db_session.refresh(gated_printer)
        # Without this the queue dispatches the next job straight onto the
        # machine the operator just said was not ready — the exact failure the
        # gate exists to prevent.
        assert gated_printer.drain_mode is True

    def test_names_the_job_in_the_drain_reason(
        self,
        db_session: Session,
        operator: User,
        gated_printer: Printer,
        pending_job,
    ) -> None:
        fleet.operator_decision(db_session, int(pending_job.id), "hold", operator)

        db_session.refresh(gated_printer)
        # The fleet view shows this string; without the job id an operator sees a
        # stopped machine and no way to tell which print stopped it.
        assert gated_printer.drain_reason == f"Operator hold after job {pending_job.id}"

    def test_reports_a_job_that_does_not_exist(
        self, db_session: Session, operator: User
    ) -> None:
        with pytest.raises(fleet.FleetError, match="queue_job_not_found"):
            fleet.operator_decision(db_session, 999_999, "release", operator)

    def test_refuses_a_job_that_is_not_waiting_at_a_gate(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        job = build_print_job(db_session, artifact, printer=printer)

        # Answering a gate that was never raised would release a printer nothing
        # was holding, or double-answer one that is already decided.
        with pytest.raises(fleet.FleetError, match="operator_decision_not_pending"):
            fleet.operator_decision(db_session, int(job.id), "release", operator)

    def test_refuses_an_answer_that_is_neither_release_nor_hold(
        self, db_session: Session, operator: User, pending_job
    ) -> None:
        with pytest.raises(fleet.FleetError, match="operator_decision_invalid"):
            fleet.operator_decision(db_session, int(pending_job.id), "later", operator)

    def test_reports_a_gated_job_with_no_printer(
        self, db_session: Session, operator: User, artifact: File
    ) -> None:
        orphan = build_print_job(
            db_session,
            artifact,
            remote_filename="unassigned.gcode",
            state=PrintJobState.COMPLETED,
            operator_gate_state=OperatorGateState.PENDING,
        )

        # The decision's whole effect is on the printer, so a gate with no
        # printer has nothing to act on and must not report success.
        with pytest.raises(fleet.FleetError, match="printer_not_found"):
            fleet.operator_decision(db_session, int(orphan.id), "release", operator)
