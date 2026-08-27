"""Defends printer rbac at the printers API integration boundary.

A regression could authorize or issue a printer operation against the wrong device.
"""

from __future__ import annotations

from ._printers_shared import (
    AsyncMock,
    Printer,
    PrinterRole,
    PrinterStatus,
    Session,
    TestClient,
    User,
    WebSocketDisconnect,
    _grant_printer,
    _user_headers,
    patch,
    pytest,
    select,
)


class TestPrinterRbac:
    def test_user_only_lists_granted_printers_and_connection_is_redacted(
        self, client: TestClient, db_session: Session
    ) -> None:
        visible = Printer(
            name="Shared",
            moonraker_url="http://shared.local:7125",
            api_key="secret",
        )
        hidden = Printer(name="Private", moonraker_url="http://private.local:7125")
        db_session.add_all([visible, hidden])
        db_session.commit()
        db_session.refresh(visible)
        headers = _user_headers(db_session, "viewer")
        _grant_printer(db_session, "viewer", visible, PrinterRole.VIEW)

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

    def test_role_change_and_revocation_take_effect_immediately(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = Printer(name="Shared", moonraker_url="http://shared.local:7125")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        user_headers = _user_headers(db_session, "operator")
        user = db_session.exec(select(User).where(User.username == "operator")).one()

        granted = client.put(
            f"/api/v1/printers/{printer.id}/permissions/{user.id}",
            json={"role": "control"},
            headers=auth_headers,
        )
        assert granted.status_code == 200
        assert granted.json()["role"] == "control"
        assert (
            client.get(f"/api/v1/printers/{printer.id}", headers=user_headers).json()[
                "access"
            ]["can_control"]
            is True
        )

        revoked = client.delete(
            f"/api/v1/printers/{printer.id}/permissions/{user.id}",
            headers=auth_headers,
        )
        assert revoked.status_code == 204
        assert (
            client.get(
                f"/api/v1/printers/{printer.id}", headers=user_headers
            ).status_code
            == 403
        )

    def test_view_role_cannot_control_printer(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = Printer(name="Shared", moonraker_url="http://shared.local:7125")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        headers = _user_headers(db_session, "viewer-control")
        _grant_printer(db_session, "viewer-control", printer, PrinterRole.VIEW)

        response = client.post(f"/api/v1/printers/{printer.id}/pause", headers=headers)

        assert response.status_code == 403
        assert response.json()["detail"] == "printer_permission_denied"

    def test_print_role_passes_printer_gate_but_not_control_gate(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = Printer(name="Shared", moonraker_url="http://shared.local:7125")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        headers = _user_headers(db_session, "print-only")
        _grant_printer(db_session, "print-only", printer, PrinterRole.PRINT)

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
        printer = Printer(name="Shared", moonraker_url="http://shared.local:7125")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        headers = _user_headers(db_session, "controller")
        _grant_printer(db_session, "controller", printer, PrinterRole.CONTROL)

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
        ids=lambda update: next(iter(update)),
    )
    def test_printer_admin_cannot_change_connection_settings(
        self,
        client: TestClient,
        db_session: Session,
        connection_update: dict[str, str],
    ) -> None:
        printer = Printer(
            name="Shared",
            moonraker_url="http://printer.local:7125",
            api_key="original-secret",
        )
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        headers = _user_headers(db_session, "delegated-admin")
        _grant_printer(db_session, "delegated-admin", printer, PrinterRole.ADMIN)

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
        printer = Printer(name="Shared", moonraker_url="http://printer.local:7125")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        headers = _user_headers(db_session, "metadata-admin")
        _grant_printer(db_session, "metadata-admin", printer, PrinterRole.ADMIN)

        response = client.patch(
            f"/api/v1/printers/{printer.id}",
            json={"name": "Renamed", "notes": "Still delegated"},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"
        assert response.json()["notes"] == "Still delegated"


class TestListPrinters:
    def test_list_empty(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/printers", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get("/api/v1/printers", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Ender 3"
        assert data[0]["status"] == PrinterStatus.UNKNOWN.value


class TestPrinterWebSocketAuth:
    def test_one_time_ticket_replaces_access_token_in_websocket_url(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ):
        printer = Printer(name="Ticketed", moonraker_url="http://printer.local")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        response = client.post(
            f"/api/v1/printers/{printer.id}/ws-ticket", headers=auth_headers
        )
        assert response.status_code == 200
        ticket = response.json()["ticket"]
        assert response.json()["expires_in"] <= 30

        with client.websocket_connect(
            f"/api/v1/printers/{printer.id}/ws?ticket={ticket}"
        ):
            pass

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws?ticket={ticket}"
            ):
                pass

        raw_token = auth_headers["Authorization"].split(" ", 1)[1]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws?token={raw_token}"
            ):
                pass


class TestCreatePrinter:
    def test_create_requires_auth(self, client: TestClient):
        resp = client.post(
            "/api/v1/printers",
            json={"name": "Ender 3", "moonraker_url": "http://10.0.0.1:7125"},
        )
        assert resp.status_code == 401

    def test_create_with_auth(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Ender 3",
                "moonraker_url": "http://10.0.0.1:7125",
                "api_key": "secret",
                "notes": "Garage printer",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Ender 3"
        assert data["moonraker_url"] == "http://10.0.0.1:7125"
        assert data["has_api_key"] is True
        assert data["notes"] == "Garage printer"
        assert data["status"] == PrinterStatus.UNKNOWN.value

    def test_create_strips_trailing_slashes(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={"name": "Prusa", "moonraker_url": "http://10.0.0.2:7125/"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["moonraker_url"] == "http://10.0.0.2:7125"

    def test_create_detects_neptune4_model(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Neptune",
                "moonraker_url": "http://10.0.0.3:7125",
                "provider_variant": "elegoo_neptune4",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["detected_model"] == "Elegoo Neptune 4 family"
        assert data["model_name"] is None

    def test_create_with_manual_model_name(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Voron",
                "moonraker_url": "http://10.0.0.4:7125",
                "model_name": "Voron 2.4",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["model_name"] == "Voron 2.4"
        assert data["detected_model"] is None


class TestGetPrinter:
    def test_get_returns_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Ender 3"

    def test_get_404(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/printers/99999", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"


class TestUpdatePrinter:
    def test_update_requires_auth(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(f"/api/v1/printers/{p.id}", json={"name": "Ender 3 Pro"})
        assert resp.status_code == 401

    def test_update_name(self, client: TestClient, auth_headers, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(
            f"/api/v1/printers/{p.id}",
            json={"name": "Ender 3 Pro"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Ender 3 Pro"

    def test_update_404(self, client: TestClient, auth_headers):
        resp = client.patch(
            "/api/v1/printers/99999",
            json={"name": "Nope"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_update_manual_model_name_overrides_display(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(
            name="Neptune",
            moonraker_url="http://10.0.0.1:7125",
            provider_variant="elegoo_neptune4",
            detected_model="Elegoo Neptune 4 family",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(
            f"/api/v1/printers/{p.id}",
            json={"model_name": "Neptune 4 Pro"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_name"] == "Neptune 4 Pro"
        assert data["detected_model"] == "Elegoo Neptune 4 family"


class TestDeletePrinter:
    def test_delete_requires_auth(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.delete(f"/api/v1/printers/{p.id}")
        assert resp.status_code == 401

    def test_delete_removes_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.delete(f"/api/v1/printers/{p.id}", headers=auth_headers)
        assert resp.status_code == 204

        resp2 = client.get(f"/api/v1/printers/{p.id}", headers=auth_headers)
        assert resp2.status_code == 404

    def test_delete_404(self, client: TestClient, auth_headers):
        resp = client.delete("/api/v1/printers/99999", headers=auth_headers)
        assert resp.status_code == 404
