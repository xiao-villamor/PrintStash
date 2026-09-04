"""Cancelling a job before it was ever sent to a printer.

A queued job never reached a machine, so removing it must not leave behind the
history of a print that did. It is marked cancelled and trashed together, which
keeps the audit trail honest without inventing a failed print in the statistics.

The part that is easy to forget is the renumbering. `queue_position` is what the
scheduler orders by, so deleting the job at position 2 has to close the gap —
otherwise every remaining job keeps a stale position and the operator's careful
reordering drifts a little further out of true with each deletion.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import File, Printer, PrintJobState, User
from app.schemas.fleet import QueueJobCreate
from app.services import fleet


def _queue(session: Session, artifact: File, printer: Printer, user: User):
    return fleet.enqueue_job(
        session,
        QueueJobCreate(
            file_id=int(artifact.id), strategy="manual", printer_id=int(printer.id)
        ),
        user,
    )


class TestDeleteQueueJob:
    def test_records_the_removal_as_a_cancellation_in_the_trash(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        job = _queue(db_session, artifact, printer, operator)

        removed = fleet.delete_queue_job(db_session, int(job.id), operator)

        # Cancelled *and* trashed: cancelled alone would show up in print
        # statistics as a print that failed, which it never was.
        assert removed.state == PrintJobState.CANCELLED
        assert removed.deleted_at is not None

    def test_closes_the_gap_it_left_in_the_queue_order(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        first = _queue(db_session, artifact, printer, operator)
        second = _queue(db_session, artifact, printer, operator)
        third = _queue(db_session, artifact, printer, operator)

        fleet.delete_queue_job(db_session, int(second.id), operator)

        # The scheduler orders by `queue_position`. Leaving a hole makes every
        # later reorder drift a little further from what the operator arranged.
        db_session.refresh(first)
        db_session.refresh(third)
        assert [first.queue_position, third.queue_position] == [1, 2]

    def test_refuses_a_job_that_is_already_printing(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        job = _queue(db_session, artifact, printer, operator)
        job.state = PrintJobState.PRINTING
        db_session.add(job)
        db_session.commit()

        with pytest.raises(fleet.FleetError, match="queue_job_not_editable"):
            fleet.delete_queue_job(db_session, int(job.id), operator)

    def test_reports_a_job_that_does_not_exist(
        self, db_session: Session, operator: User
    ) -> None:
        with pytest.raises(fleet.FleetError, match="queue_job_not_found"):
            fleet.delete_queue_job(db_session, 999_999, operator)

    def test_reports_a_job_that_was_already_removed(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        job = _queue(db_session, artifact, printer, operator)
        fleet.delete_queue_job(db_session, int(job.id), operator)

        # Not-found rather than not-editable: a trashed job is gone as far as the
        # queue is concerned, and reporting its state would confirm it existed.
        with pytest.raises(fleet.FleetError, match="queue_job_not_found"):
            fleet.delete_queue_job(db_session, int(job.id), operator)
