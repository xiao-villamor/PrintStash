"""Who may see, use, and change each printer.

Printer permissions are **per printer**, not global: a user with operator rights on the
workshop Prusa has no rights at all on the one in the office. That is what makes the fleet
shareable, and it is also what makes the negative cases matter more than the positive ones
— every endpoint must check the role on *the printer in the request*, and an endpoint that
checks the caller's rights on any printer at all would pass a naive test.

The roles are ordered: viewer reads, operator prints, manager administers. Each endpoint
names its minimum, and a role below it is a 403 rather than a filtered result — a
half-answer would read as "the printer is idle" when it means "you may not look".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    PrinterRole,
    User,
)
from tests.factories import (
    build_printer,
    printer_config,
)
from tests.integration.api.v1.printers._helpers import grant_printer, user_headers


class TestPrinterRbac:
    def test_a_granted_printer_is_listed_with_its_connection_redacted(
        self, client: TestClient, db_session: Session
    ) -> None:
        visible = printer_config(
            "Shared",
            moonraker_url="http://shared.local:7125",
            api_key="secret",
        )
        hidden = build_printer(
            db_session, name="Private", moonraker_url="http://private.local:7125"
        )
        db_session.add_all([visible, hidden])
        db_session.commit()
        db_session.refresh(visible)
        headers = user_headers(db_session, "viewer")
        grant_printer(db_session, "viewer", visible, PrinterRole.VIEW)

        response = client.get("/api/v1/printers", headers=headers)

        assert response.status_code == 200
        assert [row["name"] for row in response.json()] == ["Shared"]
        assert response.json()[0]["moonraker_url"] == ""
        assert response.json()[0]["has_api_key"] is False
        assert response.json()[0]["access"] == {
            "role": "view",
            "can_view": True,
            "can_print": False,
            "can_control": False,
            "can_admin": False,
        }
        hidden_response = client.get(f"/api/v1/printers/{hidden.id}", headers=headers)
        assert hidden_response.status_code == 403

    def test_a_role_change_takes_effect_without_a_new_token(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = build_printer(
            db_session, name="Shared", moonraker_url="http://shared.local:7125"
        )
        operator_headers = user_headers(db_session, "operator")
        user = db_session.exec(select(User).where(User.username == "operator")).one()

        granted = client.put(
            f"/api/v1/printers/{printer.id}/permissions/{user.id}",
            json={"role": "control"},
            headers=auth_headers,
        )
        assert granted.status_code == 200
        assert granted.json()["role"] == "control"
        assert (
            client.get(
                f"/api/v1/printers/{printer.id}", headers=operator_headers
            ).json()["access"]["can_control"]
            is True
        )

        revoked = client.delete(
            f"/api/v1/printers/{printer.id}/permissions/{user.id}",
            headers=auth_headers,
        )
        assert revoked.status_code == 204
        assert (
            client.get(
                f"/api/v1/printers/{printer.id}", headers=operator_headers
            ).status_code
            == 403
        )

    def test_view_role_cannot_control_printer(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session, name="Shared", moonraker_url="http://shared.local:7125"
        )
        headers = user_headers(db_session, "viewer-control")
        grant_printer(db_session, "viewer-control", printer, PrinterRole.VIEW)

        response = client.post(f"/api/v1/printers/{printer.id}/pause", headers=headers)

        assert response.status_code == 403
        assert response.json()["detail"] == "printer_permission_denied"

    def test_print_role_passes_printer_gate_but_not_control_gate(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session, name="Shared", moonraker_url="http://shared.local:7125"
        )
        headers = user_headers(db_session, "print-only")
        grant_printer(db_session, "print-only", printer, PrinterRole.PRINT)

        send_response = client.post(
            f"/api/v1/printers/{printer.id}/send",
            json={"file_id": 999, "start_print": False},
            headers=headers,
        )
        control_response = client.post(
            f"/api/v1/printers/{printer.id}/pause", headers=headers
        )

        assert send_response.status_code == 404
        assert send_response.json()["detail"] == "file_not_found"
        assert control_response.status_code == 403

    def test_control_role_can_pause_printer(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session, name="Shared", moonraker_url="http://shared.local:7125"
        )
        headers = user_headers(db_session, "controller")
        grant_printer(db_session, "controller", printer, PrinterRole.CONTROL)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.pause",
            new_callable=AsyncMock,
        ) as pause:
            pause.return_value = {"result": "ok"}
            response = client.post(
                f"/api/v1/printers/{printer.id}/pause", headers=headers
            )

        assert response.status_code == 200
        pause.assert_awaited_once()

    @pytest.mark.parametrize(
        "connection_update",
        [
            {"provider": "moonraker"},
            {"moonraker_url": "http://attacker.invalid"},
            {"api_key": "replacement"},
            {"provider_variant": "generic"},
            {"bambu_host": "attacker.invalid"},
            {"bambu_serial": "serial"},
            {"bambu_access_code": "code"},
            {"prusalink_url": "http://attacker.invalid"},
            {"prusalink_auth_mode": "api_key"},
            {"prusalink_username": "username"},
            {"prusalink_password": "password"},
            {"prusalink_api_key": "replacement"},
            {"elegoo_centauri_host": "attacker.invalid"},
            {"elegoo_centauri_access_code": "code"},
            {"elegoo_centauri_mainboard_id": "board"},
            {"octoprint_url": "http://attacker.invalid"},
            {"octoprint_api_key": "replacement"},
        ],
    )
    def test_printer_admin_cannot_change_connection_settings(
        self,
        client: TestClient,
        db_session: Session,
        connection_update: dict[str, str],
    ) -> None:
        printer = build_printer(
            db_session,
            name="Shared",
            moonraker_url="http://printer.local:7125",
            api_key="original-secret",
        )
        headers = user_headers(db_session, "delegated-admin")
        grant_printer(db_session, "delegated-admin", printer, PrinterRole.ADMIN)

        response = client.patch(
            f"/api/v1/printers/{printer.id}",
            json=connection_update,
            headers=headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "admin_required"
        db_session.refresh(printer)
        assert printer.moonraker_url == "http://printer.local:7125"
        assert printer.api_key == "original-secret"

    def test_printer_admin_can_still_change_non_connection_metadata(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session, name="Shared", moonraker_url="http://printer.local:7125"
        )
        headers = user_headers(db_session, "metadata-admin")
        grant_printer(db_session, "metadata-admin", printer, PrinterRole.ADMIN)

        response = client.patch(
            f"/api/v1/printers/{printer.id}",
            json={"name": "Renamed", "notes": "Still delegated"},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"
        assert response.json()["notes"] == "Still delegated"


class TestPrinterPermissions:
    """The endpoints that grant, change, and revoke a role on one printer."""

    def test_lists_who_has_a_role_on_the_printer(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        printer = build_printer(
            db_session, name="Shared", moonraker_url="http://shared.local:7125"
        )
        user_headers(db_session, "listed-operator")
        user = db_session.exec(
            select(User).where(User.username == "listed-operator")
        ).one()
        client.put(
            f"/api/v1/printers/{printer.id}/permissions/{user.id}",
            headers=auth_headers,
            json={"role": "control"},
        )

        response = client.get(
            f"/api/v1/printers/{printer.id}/permissions", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        listed = response.json()[0]
        assert listed["username"] == "listed-operator"
        assert listed["role"] == "control"

    def test_lists_nobody_for_a_printer_nobody_was_granted(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        printer = build_printer(
            db_session, name="Private", moonraker_url="http://private.local:7125"
        )

        response = client.get(
            f"/api/v1/printers/{printer.id}/permissions", headers=auth_headers
        )

        assert response.json() == []

    def test_changes_a_role_that_is_already_granted(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        printer = build_printer(
            db_session, name="Regraded", moonraker_url="http://regraded.local:7125"
        )
        user_headers(db_session, "regraded-user")
        user = db_session.exec(
            select(User).where(User.username == "regraded-user")
        ).one()
        client.put(
            f"/api/v1/printers/{printer.id}/permissions/{user.id}",
            headers=auth_headers,
            json={"role": "view"},
        )

        response = client.put(
            f"/api/v1/printers/{printer.id}/permissions/{user.id}",
            headers=auth_headers,
            json={"role": "admin"},
        )

        # Re-granting must update the existing row, not add a second one.
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "admin"

    def test_refuses_to_grant_a_role_to_a_user_who_does_not_exist(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        printer = build_printer(
            db_session, name="Ghost grant", moonraker_url="http://ghost.local:7125"
        )

        response = client.put(
            f"/api/v1/printers/{printer.id}/permissions/999999",
            headers=auth_headers,
            json={"role": "view"},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "user_not_found"

    def test_refuses_to_revoke_a_role_that_was_never_granted(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        printer = build_printer(
            db_session, name="Never granted", moonraker_url="http://never.local:7125"
        )
        user_headers(db_session, "ungranted-user")
        user = db_session.exec(
            select(User).where(User.username == "ungranted-user")
        ).one()

        response = client.delete(
            f"/api/v1/printers/{printer.id}/permissions/{user.id}",
            headers=auth_headers,
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "permission_not_found"

    def test_rejects_a_non_superuser_listing_permissions(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session, name="Guarded", moonraker_url="http://guarded.local:7125"
        )
        headers = user_headers(db_session, "permission-peeker")
        grant_printer(db_session, "permission-peeker", printer, PrinterRole.ADMIN)

        response = client.get(
            f"/api/v1/printers/{printer.id}/permissions", headers=headers
        )

        # Printer-admin is not the same as deployment-admin: who else has access
        # is fleet-wide information.
        assert response.status_code == 403, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = build_printer(
            db_session, name="Anonymous", moonraker_url="http://anon.local:7125"
        )

        assert (
            client.get(f"/api/v1/printers/{printer.id}/permissions").status_code == 401
        )
