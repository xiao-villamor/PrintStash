"""Printer purges preserve historical events and remove only owned configuration."""

from datetime import datetime, timezone

import pytest
from sqlmodel import select

from app.db.models import NotificationDelivery, Printer, PrintJob
from app.modules.library import trash
from tests import factories

EXPIRED = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(params=["tool", "slot", "permission", "window", "log", "file"], ids=str)
def owned_child(request, db_session, make_printer, make_user):
    printer = make_printer(trashed=EXPIRED)
    builders = {
        "tool": lambda: factories.build_printer_tool(db_session, printer),
        "slot": lambda: factories.build_material_slot(db_session, printer),
        "permission": lambda: factories.grant_printer_role(
            db_session, make_user(), printer
        ),
        "window": lambda: factories.build_printer_maintenance_window(
            db_session, printer
        ),
        "log": lambda: factories.build_printer_maintenance_log(db_session, printer),
        "file": lambda: factories.build_printer_file(db_session, printer),
    }
    return printer, builders[request.param]()


class TestHardDeletePrinter:
    def test_removes_owned_configuration(self, db_session, owned_child):
        printer, child = owned_child
        printer_id, child_id, child_type = printer.id, child.id, type(child)
        trash.hard_delete_printer(db_session, printer)
        db_session.commit()
        assert db_session.get(child_type, child_id) is None
        assert db_session.get(Printer, printer_id) is None

    @pytest.mark.parametrize(
        "snapshot", [None, "Historical name"], ids=["missing-name", "saved-name"]
    )
    def test_preserves_print_history(
        self, db_session, make_printer, make_model, make_file, make_print_job, snapshot
    ):
        printer = make_printer(trashed=EXPIRED)
        job = make_print_job(
            make_file(make_model()), printer=printer, printer_name=snapshot
        )
        job_id, printer_name = job.id, printer.name
        trash.hard_delete_printer(db_session, printer)
        db_session.commit()
        db_session.expire_all()
        history = db_session.get(PrintJob, job_id)
        assert history is not None
        assert history.printer_id is None
        assert history.printer_name == (snapshot or printer_name)

    def test_preserves_notification_history(
        self,
        db_session,
        make_printer,
        make_notification_channel,
        make_notification_delivery,
    ):
        printer = make_printer(trashed=EXPIRED)
        delivery = make_notification_delivery(
            make_notification_channel(),
            printer=printer,
            context_json='{"printer_name":"Old printer"}',
        )
        delivery_id = delivery.id
        trash.hard_delete_printer(db_session, printer)
        db_session.commit()
        db_session.expire_all()
        history = db_session.get(NotificationDelivery, delivery_id)
        assert history is not None
        assert history.printer_id is None
        assert history.context_json == '{"printer_name":"Old printer"}'

    def test_preserves_other_printers(
        self, db_session, make_printer, make_printer_file
    ):
        target = make_printer(trashed=EXPIRED)
        other = make_printer()
        other_file = make_printer_file(other)
        other_id, file_id = other.id, other_file.id

        trash.hard_delete_printer(db_session, target)
        db_session.commit()

        assert db_session.get(Printer, other_id).deleted_at is None
        assert db_session.get(type(other_file), file_id).printer_id == other_id

    def test_refuses_live_printer(self, db_session, make_printer):
        printer = make_printer()
        with pytest.raises(trash.PurgeConflictError, match="storage_cleanup_blocked"):
            trash.hard_delete_printer(db_session, printer)
        assert db_session.exec(select(Printer)).one().id == printer.id

    def test_refuses_stale_deletion_snapshot(self, db_session, make_printer):
        from sqlmodel import Session

        printer = make_printer(trashed=EXPIRED)
        printer_id = printer.id
        db_session.expunge(printer)
        with Session(db_session.get_bind()) as other:
            current = other.get(Printer, printer_id)
            current.deleted_at = None
            other.add(current)
            other.commit()
        with pytest.raises(trash.PurgeConflictError, match="storage_cleanup_blocked"):
            trash.hard_delete_printer(db_session, printer)
        assert db_session.get(Printer, printer_id).deleted_at is None


class TestGcSoftDeletedPrinters:
    def test_purges_printer_with_owned_children(self, db_session, owned_child):
        printer, child = owned_child
        printer_id, child_id, child_type = printer.id, child.id, type(child)
        result = trash.gc_soft_deleted(retention_days=30)
        db_session.expire_all()
        assert result["rows"] == 1
        assert db_session.get(Printer, printer_id) is None
        assert db_session.get(child_type, child_id) is None

    def test_continues_publication_recovery_after_printer_purge(
        self,
        db_session,
        local_storage,
        make_printer,
        make_printer_file,
        make_owned_storage_object,
    ):
        from app.db.models import StorageObjectState
        from app.modules.storage.storage_backend.runtime import get_backend

        printer = make_printer(trashed=EXPIRED)
        make_printer_file(printer)
        backend = get_backend()
        key = backend.thumbnail_key(910)
        reservation = make_owned_storage_object(
            backend=backend.backend_name,
            namespace=backend.namespace_for(key),
            key=key,
            object_kind="thumbnail",
            state=StorageObjectState.PENDING,
            token=None,
            created_at=EXPIRED,
        )

        trash.gc_soft_deleted(retention_days=30)
        db_session.refresh(reservation)

        assert reservation.state is StorageObjectState.RETIRING
