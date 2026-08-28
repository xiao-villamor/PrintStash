"""Changing a queued job after it was queued, without racing another operator.

A queue is a shared surface: two people looking at the same fleet view will both
try to reprioritise the same job. So every edit carries the version it was read
at, and a write based on a stale read is refused rather than merged — the
alternative is one operator's reordering silently undoing the other's.

The material override is the field with a consequence attached. Setting
`ALLOW_MISMATCH` says "print it anyway", and who said so is recorded, because it
is the one decision in the queue that can waste a spool on purpose. An override
with no author is an unattributable one.

`printer_id_required` is a validation the update has to repeat rather than
inherit: switching an existing job to manual routing without naming a printer
leaves it queued for nothing in particular, which reads as a stuck job.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import (
    CompatibilityPolicy,
    File,
    JobPriority,
    Printer,
    PrinterStatus,
    PrintJobState,
    RoutingStrategy,
    User,
)
from app.schemas.fleet import QueueJobCreate, QueueJobUpdate
from app.services import fleet
from tests.factories import build_printer
from tests.integration.services.fleet.conftest import load_manual_state, requiring


@pytest.fixture
def grouped_printer(db_session: Session) -> Printer:
    return build_printer(
        db_session,
        name="Fleet edges",
        moonraker_url="http://fleet-edges",
        status=PrinterStatus.READY,
        group="room-a",
    )


def _queue(
    session: Session, artifact: File, printer: Printer, user: User
) -> "fleet.PrintJob":  # type: ignore[name-defined]
    return fleet.enqueue_job(
        session,
        QueueJobCreate(
            file_id=int(artifact.id), strategy="manual", printer_id=int(printer.id)
        ),
        user,
    )


class TestUpdateQueueJob:
    def test_applies_a_new_priority(
        self,
        db_session: Session,
        operator: User,
        artifact: File,
        grouped_printer: Printer,
    ) -> None:
        job = _queue(db_session, artifact, grouped_printer, operator)

        changed = fleet.update_queue_job(
            db_session,
            int(job.id),
            QueueJobUpdate(priority=JobPriority.RUSH, queue_position=1),
            operator,
        )

        assert changed.priority == JobPriority.RUSH

    def test_applies_a_new_target_group(
        self,
        db_session: Session,
        operator: User,
        artifact: File,
        grouped_printer: Printer,
    ) -> None:
        job = _queue(db_session, artifact, grouped_printer, operator)

        changed = fleet.update_queue_job(
            db_session, int(job.id), QueueJobUpdate(target_group="room-a"), operator
        )

        assert changed.target_group == "room-a"

    def test_records_who_allowed_a_material_mismatch(
        self,
        db_session: Session,
        operator: User,
        artifact: File,
        grouped_printer: Printer,
    ) -> None:
        job = _queue(db_session, artifact, grouped_printer, operator)

        changed = fleet.update_queue_job(
            db_session,
            int(job.id),
            QueueJobUpdate(
                compatibility_policy=CompatibilityPolicy.ALLOW_MISMATCH,
                strategy=RoutingStrategy.MANUAL,
                printer_id=int(grouped_printer.id),
            ),
            operator,
        )

        # The one queue decision that can waste a spool on purpose, so it must
        # not be unattributable.
        assert changed.material_override_by == operator.id

    def test_refuses_an_edit_based_on_a_stale_read(
        self,
        db_session: Session,
        operator: User,
        artifact: File,
        grouped_printer: Printer,
    ) -> None:
        job = _queue(db_session, artifact, grouped_printer, operator)

        with pytest.raises(fleet.FleetError, match="queue_job_changed"):
            fleet.update_queue_job(
                db_session,
                int(job.id),
                QueueJobUpdate(
                    expected_updated_at=grouped_printer.updated_at,
                    priority=JobPriority.LOW,
                ),
                operator,
            )

    def test_refuses_a_switch_to_manual_routing_with_no_printer(
        self,
        db_session: Session,
        operator: User,
        artifact: File,
        grouped_printer: Printer,
    ) -> None:
        job = _queue(db_session, artifact, grouped_printer, operator)

        # A manual job with no target is queued for nothing in particular, which
        # the operator reads as a stuck job with no explanation.
        with pytest.raises(fleet.FleetError, match="printer_id_required"):
            fleet.update_queue_job(
                db_session,
                int(job.id),
                QueueJobUpdate(strategy=RoutingStrategy.MANUAL),
                operator,
            )

    def test_refuses_a_job_that_has_already_been_dispatched(
        self,
        db_session: Session,
        operator: User,
        artifact: File,
        grouped_printer: Printer,
    ) -> None:
        job = _queue(db_session, artifact, grouped_printer, operator)
        job.state = PrintJobState.PRINTING
        db_session.add(job)
        db_session.commit()

        # The bytes are already on the printer. Editing the row would leave the
        # queue describing a print that is not the one running.
        with pytest.raises(fleet.FleetError, match="queue_job_not_editable"):
            fleet.update_queue_job(
                db_session,
                int(job.id),
                QueueJobUpdate(priority=JobPriority.LOW),
                operator,
            )

    def test_refuses_a_reroute_onto_a_printer_the_filament_cannot_print(
        self,
        db_session: Session,
        operator: User,
        grouped_printer: Printer,
    ) -> None:
        artifact = requiring(db_session, material="PLA", nozzle=0.4)
        job = _queue(db_session, artifact, grouped_printer, operator)
        load_manual_state(db_session, grouped_printer, operator, material="ABS")

        # Rerouting is a second chance to start the wrong print, so it asks for
        # the same confirmation the original enqueue did.
        with pytest.raises(
            fleet.FleetError, match="material_mismatch_confirmation_required"
        ):
            fleet.update_queue_job(
                db_session,
                int(job.id),
                QueueJobUpdate(
                    strategy=RoutingStrategy.MANUAL, printer_id=int(grouped_printer.id)
                ),
                operator,
            )
