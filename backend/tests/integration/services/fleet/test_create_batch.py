"""Queueing N copies of one artifact across the fleet, as one unit.

A batch is the operator asking for ten of something, and the two properties that
make it useful are both easy to get wrong. It has to be atomic — a batch that
half-created leaves copies queued that no batch owns, so cancelling the batch
leaves prints running — and it has to *spread*, because stacking ten copies on
one printer is what the operator was avoiding by asking for a batch instead of
ten jobs.

The refusals are the same set the single-job path has, and they are here rather
than only there because a batch is the bulk entry point: a quantity limit that
only guarded single enqueues would let one request create ten thousand jobs.
Binary G-code gets its own refusal because it parses and looks printable and
then cannot be streamed to any provider.

A material mismatch refuses too, and that is deliberate for a batch above all:
ten copies on the wrong filament is ten wasted spools, so the operator confirms
once, explicitly, rather than being asked per copy.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import File, FileType, Printer, PrinterStatus, RoutingStrategy, User
from app.schemas.fleet import BatchCreate, FleetSummary
from app.services import fleet
from tests.factories import build_printer
from tests.integration.services.fleet.conftest import load_manual_state


class TestCreateBatch:
    def test_spreads_the_copies_across_the_fleet(
        self, db_session: Session, operator: User, artifact: File
    ) -> None:
        for index in range(2):
            build_printer(
                db_session,
                name=f"Batch {index}",
                moonraker_url=f"http://batch-{index}",
                status=PrinterStatus.READY,
            )

        _batch, jobs = fleet.create_batch(
            db_session,
            BatchCreate(file_id=int(artifact.id), quantity=2, strategy="least_busy"),
            operator,
        )

        # Stacking both copies on one printer is exactly what asking for a batch
        # instead of two jobs was meant to avoid.
        assert len({job.printer_id for job in jobs}) == 2

    def test_numbers_the_copies_so_the_operator_can_tell_them_apart(
        self, db_session: Session, operator: User, artifact: File, printer: Printer
    ) -> None:
        batch, jobs = fleet.create_batch(
            db_session,
            BatchCreate(file_id=int(artifact.id), quantity=2, strategy="least_busy"),
            operator,
        )

        assert batch.quantity == 2
        assert [job.copy_index for job in jobs] == [1, 2]

    def test_shows_each_printers_next_job_in_the_fleet_summary(
        self, db_session: Session, operator: User, artifact: File
    ) -> None:
        for index in range(2):
            build_printer(
                db_session,
                name=f"Batch {index}",
                moonraker_url=f"http://batch-{index}",
                status=PrinterStatus.READY,
            )
        fleet.create_batch(
            db_session,
            BatchCreate(file_id=int(artifact.id), quantity=2, strategy="least_busy"),
            operator,
        )

        summary = FleetSummary(**fleet.fleet_summary(db_session))

        # The spread is only visible to the operator through this view, so a
        # batch that queued correctly and reported nothing is still broken.
        assert {row.name for row in summary.printers} == {"Batch 0", "Batch 1"}
        assert all(row.next_job_id is not None for row in summary.printers)

    def test_refuses_a_batch_the_loaded_filament_cannot_print(
        self,
        db_session: Session,
        operator: User,
        printer: Printer,
        pla_artifact: File,
    ) -> None:
        load_manual_state(db_session, printer, operator, material="ABS")

        # Ten copies on the wrong filament is ten wasted spools, so the operator
        # confirms once and explicitly rather than per copy.
        with pytest.raises(
            fleet.FleetError, match="material_mismatch_confirmation_required"
        ):
            fleet.create_batch(
                db_session,
                BatchCreate(
                    file_id=int(pla_artifact.id),
                    quantity=1,
                    strategy=RoutingStrategy.MANUAL,
                    printer_id=int(printer.id),
                ),
                operator,
            )

    def test_reports_an_artifact_that_does_not_exist(
        self, db_session: Session, operator: User
    ) -> None:
        with pytest.raises(fleet.FleetError, match="file_not_found"):
            fleet.create_batch(
                db_session, BatchCreate(file_id=999_999, quantity=1), operator
            )

    def test_refuses_a_quantity_past_the_limit(
        self, db_session: Session, operator: User, artifact: File
    ) -> None:
        # A batch is the bulk entry point: without a cap here one request could
        # create ten thousand jobs.
        with pytest.raises(fleet.FleetError, match="batch_quantity_exceeds_limit"):
            fleet.create_batch(
                db_session,
                BatchCreate(file_id=int(artifact.id), quantity=101),
                operator,
            )

    def test_refuses_a_file_that_is_not_gcode(
        self, db_session: Session, operator: User, artifact: File
    ) -> None:
        artifact.file_type = FileType.STL
        db_session.add(artifact)
        db_session.commit()

        with pytest.raises(fleet.FleetError, match="file_not_gcode"):
            fleet.create_batch(
                db_session, BatchCreate(file_id=int(artifact.id), quantity=1), operator
            )

    def test_refuses_binary_gcode(
        self, db_session: Session, operator: User, artifact: File
    ) -> None:
        artifact.original_filename = "binary.bgcode"
        db_session.add(artifact)
        db_session.commit()

        # `.bgcode` is a G-code file by type and unstreamable by every provider,
        # so it passes the type check and fails at dispatch without this.
        with pytest.raises(fleet.FleetError, match="binary_gcode_not_printable"):
            fleet.create_batch(
                db_session, BatchCreate(file_id=int(artifact.id), quantity=1), operator
            )
