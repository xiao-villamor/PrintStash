"""Dispatch enforces actor authority before touching a provider outside HTTP."""

import asyncio

import pytest
from sqlmodel import select

from app.core.errors import ErrorKind, OperationError
from app.db.models import PrintJob
from app.modules.printing.dispatch import send_to_printer
from app.schemas.printers import SendToPrinter


class TestDispatchAuthority:
    def test_inactive_worker_actor_cannot_contact_a_printer(
        self, db_session, make_user, make_printer, make_model, make_file
    ):
        actor = make_user(superuser=True, active=False)
        printer = make_printer()
        model = make_model()
        artifact = make_file(model)
        contacted = []

        def unavailable(*args):
            contacted.append(args)
            raise AssertionError("dependencies must not be accessed before authority")

        with pytest.raises(OperationError) as raised:
            asyncio.run(
                send_to_printer(
                    printer.id,
                    SendToPrinter(file_id=artifact.id),
                    actor,
                    db_session,
                    provider_builder=unavailable,
                    backend_provider=unavailable,
                )
            )

        assert raised.value.kind is ErrorKind.FORBIDDEN
        assert contacted == []
        assert db_session.exec(select(PrintJob)).all() == []


class TestSlicedDispatch:
    @pytest.mark.parametrize("delivery", ["send", "queue", "batch"])
    @pytest.mark.parametrize(
        ("plate", "code"),
        [(None, "plate_selection_required"), (99, "embedded_gcode_not_found")],
    )
    def test_refuses_unresolved_plate_before_recording_work(
        self,
        client,
        auth_headers,
        db_session,
        stored,
        make_printer,
        delivery,
        plate,
        code,
    ):
        from tests.fixtures.three_mf_projects import build_sliced_project

        artifact = stored("project.gcode.3mf", build_sliced_project())
        printer = make_printer()
        payload = {"file_id": artifact.id, "plate_index": plate}
        if delivery == "send":
            url = f"/api/v1/printers/{printer.id}/send"
        else:
            payload.update({"strategy": "manual", "printer_id": printer.id})
            url = (
                "/api/v1/fleet/queue"
                if delivery == "queue"
                else "/api/v1/fleet/batches"
            )
            if delivery == "batch":
                payload["quantity"] = 2
        response = client.post(url, headers=auth_headers, json=payload)
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == code
        assert db_session.exec(select(PrintJob)).all() == []

    def test_refuses_a_plate_on_plain_gcode(
        self, client, auth_headers, stored, make_printer
    ):
        artifact = stored("part.gcode", b"G28\n")
        printer = make_printer()
        response = client.post(
            f"/api/v1/printers/{printer.id}/send",
            headers=auth_headers,
            json={"file_id": artifact.id, "plate_index": 1},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "plate_not_supported"
