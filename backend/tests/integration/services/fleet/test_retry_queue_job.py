"""Putting a failed job back in the queue, and refusing the ones that cannot be.

A retry re-runs routing from scratch rather than reusing the printer that failed:
the machine may now be draining, in maintenance, or loaded with different
filament, and sending the job straight back at it is how a failing printer
collects a pile of repeat failures.

`retryable` is the flag that separates a failure we understand from one we do
not. A dispatch that failed *before* the printer accepted anything is safe to
send again; one that failed after, or whose outcome is ambiguous, is not — a
retry there could start a second physical print of a job already running. So
`queue_job_not_retryable` is a refusal on purpose, not a missing feature.
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


class TestRetryQueueJob:
    def test_returns_a_retryable_failure_to_the_queue(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        job = _queue(db_session, artifact, printer, operator)
        job.state = PrintJobState.FAILED
        job.retryable = True
        job.error = "connection refused"
        db_session.add(job)
        db_session.commit()

        retried = fleet.retry_queue_job(db_session, int(job.id), operator)

        assert retried.state == PrintJobState.QUEUED
        assert retried.error is None

    def test_clears_the_retryable_flag_so_it_is_not_retried_in_a_loop(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        job = _queue(db_session, artifact, printer, operator)
        job.state = PrintJobState.FAILED
        job.retryable = True
        db_session.add(job)
        db_session.commit()

        retried = fleet.retry_queue_job(db_session, int(job.id), operator)

        # Each failure earns its own retry decision; a flag that survived would
        # let one job cycle indefinitely against a printer that keeps refusing.
        assert retried.retryable is False

    def test_refuses_a_failure_whose_outcome_is_not_known_to_be_safe(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        job = _queue(db_session, artifact, printer, operator)
        job.state = PrintJobState.FAILED
        job.retryable = False
        db_session.add(job)
        db_session.commit()

        # The printer may already be printing it. A second dispatch would be a
        # second physical print.
        with pytest.raises(fleet.FleetError, match="queue_job_not_retryable"):
            fleet.retry_queue_job(db_session, int(job.id), operator)

    def test_refuses_a_job_that_has_not_failed(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        job = _queue(db_session, artifact, printer, operator)

        with pytest.raises(fleet.FleetError, match="queue_job_not_retryable"):
            fleet.retry_queue_job(db_session, int(job.id), operator)

    def test_reports_a_job_that_does_not_exist(
        self, db_session: Session, operator: User
    ) -> None:
        with pytest.raises(fleet.FleetError, match="queue_job_not_found"):
            fleet.retry_queue_job(db_session, 999_999, operator)
