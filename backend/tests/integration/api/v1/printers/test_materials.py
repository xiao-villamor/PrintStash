"""What is loaded in each of a printer's tools, and correcting it by hand.

A printer's material state is the answer to "can this machine print this file right now",
and it comes from two places that disagree: what the provider reports, and what the
operator says after changing a spool the machine did not notice. The manual override is
therefore a **full replacement** of the operator-owned rows, not a merge — a merge would
leave a slot the operator meant to clear still claiming PLA.

Because two people can be editing the same fleet, the replacement is guarded by the state
it was read from. A `PUT` carrying a stale `expected_updated_at` is a 409, not a
last-writer-wins overwrite: silently clobbering somebody else's correction is how a
machine ends up printing into the wrong filament.

The validation refusals are all 400 and all name what is wrong — a duplicate key, a slot
pointing at a tool that does not exist, a loaded slot with no material — because the
operator is filling in a form and needs to know which field to fix.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import Printer, PrinterRole, PrinterStatus
from tests.factories import build_printer
from tests.integration.api.v1.printers._helpers import grant_printer, user_headers

TOOL = {"tool_key": "tool0", "label": "Tool 0", "nozzle_diameter_mm": 0.4}
LOADED_SLOT = {
    "slot_key": "feed",
    "label": "Feed",
    "tool_key": "tool0",
    "state": "loaded",
    "material_type": "PLA",
}


@pytest.fixture
def printer(db_session: Session) -> Printer:
    row = build_printer(
        db_session,
        name="API material",
        moonraker_url="http://api-material",
        status=PrinterStatus.READY,
    )
    return row


def _state(client: TestClient, printer: Printer, headers: dict[str, str]) -> dict:
    response = client.get(
        f"/api/v1/printers/{printer.id}/material-state", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestGetPrinterMaterialState:
    def test_reports_the_printers_whole_material_inventory(
        self, client: TestClient, auth_headers, printer: Printer
    ) -> None:
        response = client.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert "tools" in response.json()
        assert "slots" in response.json()

    def test_reports_a_printer_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.get(
            "/api/v1/printers/999999/material-state", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "printer_not_found"

    def test_reports_a_printer_that_was_deleted(
        self, client: TestClient, db_session: Session, auth_headers, printer: Printer
    ) -> None:
        printer.deleted_at = utcnow()
        db_session.add(printer)
        db_session.commit()

        response = client.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=auth_headers
        )

        assert response.status_code == 404, response.text

    def test_reports_a_printer_the_service_cannot_find(
        self,
        client: TestClient,
        auth_headers,
        printer: Printer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import printers as printers_api

        def gone(*_args: object, **_kwargs: object):
            raise printers_api.materials.MaterialStateError("printer_not_found")

        monkeypatch.setattr(printers_api.materials, "read_material_state", gone)

        response = client.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=auth_headers
        )

        # The RBAC gate passes first, so this is the service's own refusal.
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "printer_not_found"

    def test_rejects_a_caller_with_no_role_on_the_printer(
        self, client: TestClient, db_session: Session, printer: Printer
    ) -> None:
        headers = user_headers(db_session, "material-stranger")

        response = client.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=headers
        )

        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, printer: Printer
    ) -> None:
        assert (
            client.get(f"/api/v1/printers/{printer.id}/material-state").status_code
            == 401
        )


class TestReplaceManualMaterialState:
    def test_records_what_the_operator_loaded(
        self, client: TestClient, auth_headers, printer: Printer
    ) -> None:
        state = _state(client, printer, auth_headers)

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=auth_headers,
            json={
                "expected_updated_at": state["updated_at"],
                "tools": [TOOL],
                "slots": [LOADED_SLOT],
            },
        )

        assert response.status_code == 200, response.text
        assert [row["material_type"] for row in response.json()["slots"]] == ["PLA"]

    def test_replaces_the_operators_previous_answer_rather_than_merging(
        self, client: TestClient, auth_headers, printer: Printer
    ) -> None:
        state = _state(client, printer, auth_headers)
        client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=auth_headers,
            json={
                "expected_updated_at": state["updated_at"],
                "tools": [TOOL],
                "slots": [LOADED_SLOT],
            },
        )

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=auth_headers,
            json={"tools": [TOOL], "slots": []},
        )

        # A merge would leave a slot the operator meant to clear still claiming PLA.
        assert response.status_code == 200, response.text
        assert response.json()["slots"] == []

    def test_refuses_a_write_based_on_a_stale_read(
        self, client: TestClient, auth_headers, printer: Printer
    ) -> None:
        state = _state(client, printer, auth_headers)
        client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=auth_headers,
            json={
                "expected_updated_at": state["updated_at"],
                "tools": [TOOL],
                "slots": [LOADED_SLOT],
            },
        )

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=auth_headers,
            json={"expected_updated_at": state["updated_at"]},
        )

        # Clobbering somebody else's correction is how a machine prints into the
        # wrong filament.
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "material_state_changed"

    def test_refuses_two_slots_with_the_same_key(
        self, client: TestClient, auth_headers, printer: Printer
    ) -> None:
        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=auth_headers,
            json={
                "slots": [
                    {"slot_key": "same", "label": "One"},
                    {"slot_key": "same", "label": "Two"},
                ]
            },
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "material_slot_duplicate"

    def test_refuses_a_slot_pointing_at_a_tool_that_is_not_there(
        self, client: TestClient, auth_headers, printer: Printer
    ) -> None:
        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=auth_headers,
            json={
                "tools": [TOOL],
                "slots": [{"slot_key": "feed", "label": "Feed", "tool_key": "tool9"}],
            },
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "material_slot_tool_unknown"

    def test_refuses_a_loaded_slot_with_no_material(
        self, client: TestClient, auth_headers, printer: Printer
    ) -> None:
        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=auth_headers,
            json={
                "tools": [TOOL],
                "slots": [
                    {
                        "slot_key": "feed",
                        "label": "Feed",
                        "tool_key": "tool0",
                        "state": "loaded",
                    }
                ],
            },
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "loaded_material_type_required"

    def test_reports_a_printer_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.put(
            "/api/v1/printers/999999/material-state/manual",
            headers=auth_headers,
            json={"tools": [], "slots": []},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "printer_not_found"

    def test_rejects_a_caller_who_may_only_view_the_printer(
        self, client: TestClient, db_session: Session, printer: Printer
    ) -> None:
        headers = user_headers(db_session, "material-viewer")
        grant_printer(db_session, "material-viewer", printer, PrinterRole.VIEW)

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=headers,
            json={"tools": [], "slots": []},
        )

        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, printer: Printer
    ) -> None:
        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json={"tools": [], "slots": []},
        )

        assert response.status_code == 401, response.text

    def test_reports_a_printer_the_service_cannot_find(
        self,
        client: TestClient,
        auth_headers,
        printer: Printer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import printers as printers_api

        def gone(*_args: object, **_kwargs: object):
            raise printers_api.materials.MaterialStateError("printer_not_found")

        monkeypatch.setattr(printers_api.materials, "replace_manual_state", gone)

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            headers=auth_headers,
            json={"tools": [], "slots": []},
        )

        assert response.status_code == 404, response.text
