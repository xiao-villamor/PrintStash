"""Identityless Bambu reports must describe a run, not a new job per tick.

Filename fallback preserves typed identities, printer boundaries and real
reprints. Repeated completion enqueues its notification only once.
"""

import pytest
from sqlmodel import select

from app.db.models import (
    NotificationDelivery,
    NotificationEventType,
    PrinterProvider,
    PrintJob,
    PrintJobState,
)
from app.db.session import get_session_factory
from app.modules.administration.runtime_config import set_notifications_enabled
from app.modules.printing.printer_hub import PrinterHub
from app.runtime.realtime import InProcessBus


@pytest.fixture
def zero_ids():
    return {
        "external_task_id": "0",
        "external_subtask_id": "0",
        "external_project_id": "0",
    }


@pytest.fixture(params=["zero", "absent"], ids=str)
def identityless_stats(request, zero_ids):
    return zero_ids if request.param == "zero" else {}


class TestSyncActiveJobDb:
    @pytest.mark.parametrize(
        "state,expected",
        [
            pytest.param("printing", PrintJobState.PRINTING, id="printing"),
            pytest.param("paused", PrintJobState.PAUSED, id="paused"),
            pytest.param("complete", PrintJobState.COMPLETED, id="complete"),
            pytest.param("cancelled", PrintJobState.CANCELLED, id="cancelled"),
            pytest.param("error", PrintJobState.FAILED, id="error"),
        ],
    )
    def test_repeated_reports_reuse_one_job(
        self, db_session, hub, make_printer, identityless_stats, state, expected
    ):
        printer = make_printer(provider=PrinterProvider.BAMBU_LAN)

        hub._sync_active_job_db(printer.id, state, "holder", 0.5, identityless_stats)
        hub._sync_active_job_db(printer.id, state, "holder", 0.5, identityless_stats)
        hub._sync_active_job_db(printer.id, state, "holder", 0.5, identityless_stats)

        rows = db_session.exec(
            select(PrintJob).where(PrintJob.printer_id == printer.id)
        ).all()
        assert len(rows) == 1
        assert rows[0].source == "external"
        assert rows[0].state == expected

    @pytest.mark.parametrize(
        "state", ["printing", "paused", "complete", "cancelled", "error"], ids=str
    )
    def test_restarted_hub_reuses_existing_job(
        self, db_session, hub, make_printer, identityless_stats, state
    ):
        printer = make_printer(provider=PrinterProvider.BAMBU_LAN)
        hub._sync_active_job_db(printer.id, state, "holder", 0.5, identityless_stats)
        original = db_session.exec(
            select(PrintJob).where(PrintJob.printer_id == printer.id)
        ).one()
        restarted = PrinterHub(InProcessBus(), session_factory=get_session_factory())

        restarted._sync_active_job_db(
            printer.id, state, "holder", 0.5, identityless_stats
        )

        rows = db_session.exec(
            select(PrintJob).where(PrintJob.printer_id == printer.id)
        ).all()
        assert [row.id for row in rows] == [original.id]

    @pytest.mark.parametrize(
        "state,expected",
        [
            pytest.param("printing", PrintJobState.PRINTING, id="printing"),
            pytest.param("paused", PrintJobState.PAUSED, id="paused"),
        ],
    )
    def test_observed_reprint_creates_new_job(
        self, db_session, hub, make_printer, zero_ids, state, expected
    ):
        printer = make_printer(provider=PrinterProvider.BAMBU_LAN)
        hub._sync_active_job_db(printer.id, "printing", "holder", 0.5, zero_ids)
        hub._sync_active_job_db(printer.id, "complete", "holder", 1.0, zero_ids)
        hub._sync_active_job_db(printer.id, "complete", "holder", 1.0, zero_ids)

        hub._sync_active_job_db(printer.id, state, "holder", 0.1, zero_ids)

        rows = db_session.exec(
            select(PrintJob)
            .where(PrintJob.printer_id == printer.id)
            .order_by(PrintJob.id)
        ).all()
        assert [row.state for row in rows] == [PrintJobState.COMPLETED, expected]
        assert rows[0].finished_at is not None
        assert rows[1].finished_at is None

    @pytest.mark.parametrize(
        "field",
        ["external_task_id", "external_subtask_id", "external_project_id"],
        ids=str,
    )
    @pytest.mark.parametrize("state", ["printing", "complete"], ids=str)
    def test_identityless_report_preserves_typed_external_job(
        self, db_session, hub, make_printer, zero_ids, field, state
    ):
        printer = make_printer(provider=PrinterProvider.BAMBU_LAN)
        hub._sync_active_job_db(printer.id, state, "holder", 0.5, {field: "typed-run"})

        hub._sync_active_job_db(printer.id, state, "holder", 0.5, zero_ids)
        hub._sync_active_job_db(printer.id, state, "holder", 0.5, zero_ids)

        rows = db_session.exec(
            select(PrintJob)
            .where(PrintJob.printer_id == printer.id)
            .order_by(PrintJob.id)
        ).all()
        assert len(rows) == 2
        assert getattr(rows[0], field) == "typed-run"
        assert getattr(rows[1], field) == "0"

    def test_typed_report_does_not_adopt_identityless_job(
        self, db_session, hub, make_printer, zero_ids
    ):
        printer = make_printer(provider=PrinterProvider.BAMBU_LAN)
        hub._sync_active_job_db(printer.id, "printing", "holder", 0.5, zero_ids)

        hub._sync_active_job_db(
            printer.id, "printing", "holder", 0.5, {"external_task_id": "typed-run"}
        )

        rows = db_session.exec(
            select(PrintJob)
            .where(PrintJob.printer_id == printer.id)
            .order_by(PrintJob.id)
        ).all()
        assert [row.external_task_id for row in rows] == ["0", "typed-run"]

    def test_identityless_report_reuses_vault_job(
        self,
        db_session,
        hub,
        make_printer,
        make_model,
        make_file,
        make_print_job,
        zero_ids,
    ):
        printer = make_printer(provider=PrinterProvider.BAMBU_LAN)
        job = make_print_job(
            make_file(make_model()),
            printer=printer,
            source="vault",
            state=PrintJobState.PRINTING,
            remote_filename="holder",
            external_task_id="dispatched-task",
        )
        job_id = job.id

        hub._sync_active_job_db(printer.id, "printing", "holder", 0.5, zero_ids)
        hub._sync_active_job_db(printer.id, "complete", "holder", 1.0, zero_ids)
        db_session.expire_all()

        rows = db_session.exec(
            select(PrintJob).where(PrintJob.printer_id == printer.id)
        ).all()
        assert [row.id for row in rows] == [job_id]
        assert rows[0].state is PrintJobState.COMPLETED
        assert rows[0].external_task_id == "dispatched-task"

    def test_different_filenames_remain_distinct(
        self, db_session, hub, make_printer, zero_ids
    ):
        printer = make_printer(provider=PrinterProvider.BAMBU_LAN)
        hub._sync_active_job_db(printer.id, "complete", "holder", 1.0, zero_ids)

        hub._sync_active_job_db(printer.id, "complete", "bracket", 1.0, zero_ids)

        rows = db_session.exec(
            select(PrintJob).where(PrintJob.printer_id == printer.id)
        ).all()
        assert {row.remote_filename for row in rows} == {"holder", "bracket"}

    def test_different_printers_remain_distinct(
        self, db_session, hub, make_printer, zero_ids
    ):
        first = make_printer(provider=PrinterProvider.BAMBU_LAN)
        second = make_printer(provider=PrinterProvider.BAMBU_LAN)
        hub._sync_active_job_db(first.id, "complete", "holder", 1.0, zero_ids)

        hub._sync_active_job_db(second.id, "complete", "holder", 1.0, zero_ids)

        rows = db_session.exec(select(PrintJob)).all()
        assert {row.printer_id for row in rows} == {first.id, second.id}

    def test_repeated_completion_enqueues_one_notification(
        self, db_session, hub, make_printer, make_notification_channel, zero_ids
    ):
        printer = make_printer(provider=PrinterProvider.BAMBU_LAN)
        set_notifications_enabled(db_session, True)
        make_notification_channel(events=[NotificationEventType.PRINT_COMPLETED.value])

        hub._sync_active_job_db(printer.id, "complete", "holder", 1.0, zero_ids)
        hub._sync_active_job_db(printer.id, "complete", "holder", 1.0, zero_ids)
        hub._sync_active_job_db(printer.id, "complete", "holder", 1.0, zero_ids)

        deliveries = db_session.exec(
            select(NotificationDelivery).where(
                NotificationDelivery.printer_id == printer.id
            )
        ).all()
        assert len(deliveries) == 1
        assert deliveries[0].event_type is NotificationEventType.PRINT_COMPLETED
