"""Tests for Printers API router (FastAPI TestClient)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect

from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    Printer,
    PrinterFile,
    PrinterPermission,
    PrinterProvider,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    User,
)
from app.services.auth import create_access_token, hash_password
from app.services.printer_jobs import PrinterJobError
from app.services.printer_provider import ProviderError


def _user_headers(
    db_session: Session,
    username: str,
    *,
    is_superuser: bool = False,
    scope: str = "write",
) -> dict[str, str]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope=scope)
    return {"Authorization": f"Bearer {token}"}


def _grant_printer(
    db_session: Session, username: str, printer: Printer, role: PrinterRole
) -> None:
    user = db_session.exec(select(User).where(User.username == username)).one()
    db_session.add(PrinterPermission(user_id=user.id, printer_id=printer.id, role=role))
    db_session.commit()


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


class TestPrinterControl:
    def test_pause_requires_auth(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        resp = client.post(f"/api/v1/printers/{p.id}/pause")
        assert resp.status_code == 401

    def test_pause_sends_to_moonraker(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.pause",
            new_callable=AsyncMock,
        ) as mock_pause:
            mock_pause.return_value = {"result": "ok"}
            resp = client.post(f"/api/v1/printers/{p.id}/pause", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
            mock_pause.assert_called_once()

    def test_resume_sends_to_moonraker(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.resume",
            new_callable=AsyncMock,
        ) as mock_resume:
            mock_resume.return_value = {"result": "ok"}
            resp = client.post(f"/api/v1/printers/{p.id}/resume", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    def test_cancel_sends_to_moonraker(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.cancel",
            new_callable=AsyncMock,
        ) as mock_cancel:
            mock_cancel.return_value = {"result": "ok"}
            resp = client.post(f"/api/v1/printers/{p.id}/cancel", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    def test_cancel_marks_active_job_cancelled_without_polling_transition(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(
            name="OctoPrint",
            provider="octoprint",
            octoprint_url="http://octo",
            octoprint_api_key="key",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        model = Model(name="Cancel", slug="cancel-job", hash="c" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        file = File(
            model_id=model.id,
            path="/data/cube.gcode",
            original_filename="cube.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=10,
            sha256="d" * 64,
        )
        db_session.add(file)
        db_session.commit()
        db_session.refresh(file)
        job = PrintJob(
            printer_id=p.id,
            file_id=file.id,
            model_id=model.id,
            remote_filename="cube.gcode",
            state=PrintJobState.PRINTING,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)

        with patch(
            "app.services.printer_provider.OctoPrintProvider.cancel",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ):
            resp = client.post(f"/api/v1/printers/{p.id}/cancel", headers=auth_headers)

        assert resp.status_code == 200
        db_session.refresh(job)
        assert job.state == PrintJobState.CANCELLED
        assert job.finished_at is not None

    def test_set_temperature_builds_gcode(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.run_gcode",
            new_callable=AsyncMock,
        ) as mock_gcode:
            mock_gcode.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/temperature",
                json={"heater": "bed", "target": 60},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            mock_gcode.assert_called_once_with("M140 S60")

    def test_home_subset_axes_builds_gcode(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.run_gcode",
            new_callable=AsyncMock,
        ) as mock_gcode:
            mock_gcode.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/home",
                json={"axes": "xy"},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            mock_gcode.assert_called_once_with("G28 X Y")

    def test_emergency_stop_calls_provider(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.emergency_stop",
            new_callable=AsyncMock,
        ) as mock_estop:
            mock_estop.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/emergency_stop", headers=auth_headers
            )
            assert resp.status_code == 200
            mock_estop.assert_called_once()

    def test_set_temperature_rejected_for_bambu(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(
            name="Bambu",
            provider=PrinterProvider.BAMBU_LAN,
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/temperature",
            json={"heater": "extruder", "target": 200},
            headers=auth_headers,
        )
        assert resp.status_code == 409


class TestBambuPrinter:
    def test_create_bambu_with_required_fields(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Bambu P1S",
                "provider": "bambu_lan",
                "bambu_host": "192.168.1.50",
                "bambu_serial": "SN123",
                "bambu_access_code": "access",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "bambu_lan"
        assert body["capabilities"]["can_upload"] is True
        assert body["capabilities"]["can_start"] is True
        assert body["capabilities"]["can_pause"] is True
        assert body["capabilities"]["support_level"] == "beta"
        assert "list_files" in body["capabilities"]["unsupported_actions"]

    def test_create_bambu_missing_fields_422(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={"name": "Bambu", "provider": "bambu_lan"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestPrusaLinkPrinter:
    def test_create_digest_credentials_are_redacted(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Prusa MK4",
                "provider": "prusalink",
                "prusalink_url": "http://mk4.local/",
                "prusalink_auth_mode": "digest",
                "prusalink_username": "maker",
                "prusalink_password": "secret",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "prusalink"
        assert body["prusalink_url"] == "http://mk4.local"
        assert body["prusalink_username"] == "maker"
        assert body["has_prusalink_password"] is True
        assert body["has_prusalink_api_key"] is False
        assert "prusalink_password" not in body
        assert body["capabilities"]["support_level"] == "beta"
        row = db_session.exec(select(Printer).where(Printer.name == "Prusa MK4")).one()
        assert row.prusalink_password == "secret"

    def test_create_api_key_credentials_are_redacted(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Prusa MINI",
                "provider": "prusalink",
                "prusalink_url": "http://mini.local",
                "prusalink_auth_mode": "api_key",
                "prusalink_api_key": "legacy-key",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["has_prusalink_api_key"] is True
        assert "prusalink_api_key" not in body

    def test_create_requires_credentials_for_selected_mode(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Prusa",
                "provider": "prusalink",
                "prusalink_url": "http://prusa.local",
                "prusalink_auth_mode": "digest",
                "prusalink_username": "maker",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_elegoo_neptune_variant_uses_moonraker(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Neptune 4 Plus",
                "provider": "moonraker",
                "provider_variant": "elegoo_neptune4",
                "moonraker_url": "http://neptune.local:7125",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "moonraker"
        assert body["provider_variant"] == "elegoo_neptune4"
        assert body["capabilities"]["support_level"] == "stable"


class TestOctoPrintPrinter:
    def test_create_credentials_are_redacted(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "OctoPi",
                "provider": "octoprint",
                "octoprint_url": "http://octopi.local/",
                "octoprint_api_key": "secret-key",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "octoprint"
        assert body["octoprint_url"] == "http://octopi.local"
        assert body["has_octoprint_api_key"] is True
        assert "octoprint_api_key" not in body
        assert body["capabilities"]["support_level"] == "beta"
        row = db_session.exec(select(Printer).where(Printer.name == "OctoPi")).one()
        assert row.octoprint_api_key == "secret-key"

    def test_create_requires_url_and_api_key(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={"name": "OctoPi", "provider": "octoprint"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestElegooCentauriPrinter:
    def test_create_original_carbon_without_access_code(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Centauri Carbon",
                "provider": "elegoo_centauri",
                "provider_variant": "elegoo_centauri_carbon",
                "elegoo_centauri_host": "192.168.1.50",
                "elegoo_centauri_mainboard_id": "mainboard-123",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "elegoo_centauri"
        assert body["provider_variant"] == "elegoo_centauri_carbon"
        assert body["elegoo_centauri_host"] == "192.168.1.50"
        assert body["elegoo_centauri_mainboard_id"] == "mainboard-123"
        assert body["capabilities"]["can_live_status"] is True
        assert body["capabilities"]["can_upload"] is True

    def test_create_carbon_2_redacts_access_code(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Centauri Carbon 2",
                "provider": "elegoo_centauri",
                "provider_variant": "elegoo_centauri_carbon_2",
                "elegoo_centauri_host": "192.168.1.51",
                "elegoo_centauri_access_code": "ABC123",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["has_elegoo_centauri_access_code"] is True
        assert "elegoo_centauri_access_code" not in body
        row = db_session.exec(
            select(Printer).where(Printer.name == "Centauri Carbon 2")
        ).one()
        assert row.elegoo_centauri_access_code == "ABC123"

    def test_carbon_2_requires_access_code(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Centauri Carbon 2",
                "provider": "elegoo_centauri",
                "provider_variant": "elegoo_centauri_carbon_2",
                "elegoo_centauri_host": "192.168.1.51",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestBambuPrinterOperations:
    def test_bambu_send_uploads_when_ready(
        self, client: TestClient, db_session: Session, auth_headers, tmp_path
    ):
        from app.db.models import File, Model

        m = Model(name="Model", slug="model-bambu", hash="x" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)

        f = File(
            model_id=m.id,
            path="/data/model.gcode",
            original_filename="model.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="y" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)

        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        local = tmp_path / "model.gcode"
        local.write_text("G28\n")

        class FakeBackend:
            def exists(self, _path):
                return True

            def download_to_path(self, _path, _target):
                return local

        with (
            patch("app.api.v1.printers.get_backend", return_value=FakeBackend()),
            patch(
                "app.services.printer_provider.BambuLanProvider.query_status",
                new_callable=AsyncMock,
                return_value={
                    "result": {"status": {"print_stats": {"state": "standby"}}}
                },
            ),
            patch(
                "app.services.printer_provider.BambuLanProvider.upload",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ) as upload,
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["state"] == "completed"
        upload.assert_awaited_once()

    def test_bambu_send_rejects_busy_printer(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new_callable=AsyncMock,
            return_value={"result": {"status": {"print_stats": {"state": "printing"}}}},
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": 999, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "printer_not_ready"

    def test_send_rejects_binary_bgcode(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        # A .bgcode file is indexed (file_type "gcode") for its metadata, but
        # Moonraker/Klipper can't print binary G-code — the send must 400.
        from app.db.models import File, Model

        m = Model(name="Model", slug="model-bgcode", hash="b" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)

        f = File(
            model_id=m.id,
            path="/data/model.bgcode",
            original_filename="model.bgcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="z" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)

        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": f.id, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "binary_gcode_not_printable"

    def test_bambu_pause_calls_provider(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        with patch(
            "app.services.printer_provider.BambuLanProvider.pause",
            new_callable=AsyncMock,
        ) as mock_pause:
            mock_pause.return_value = {"ok": True}
            resp = client.post(f"/api/v1/printers/{p.id}/pause", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    def test_bambu_diagnostics_reports_beta_capabilities(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"result": {"status": {}}}
            resp = client.get(
                f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["support_level"] == "beta"
        assert body["capabilities"]["can_upload"] is True
        assert body["capabilities"]["can_start"] is True
        assert "list_files" in body["unsupported_actions"]
        assert body["ok"] is True
        assert [check["name"] for check in body["checks"]] == [
            "configuration",
            "provider_info",
            "live_status",
        ]

    def test_diagnostics_timeout_returns_check_failure(
        self, client: TestClient, auth_headers, db_session: Session, monkeypatch
    ):
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        monkeypatch.setattr(
            "app.api.v1.printers._DIAGNOSTIC_CHECK_TIMEOUT_SECONDS", 0.01
        )

        async def slow_status(_self):
            await asyncio.sleep(1)
            return {"result": {"status": {}}}

        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new=slow_status,
        ):
            resp = client.get(
                f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers
            )

        assert resp.status_code == 200
        body = resp.json()
        live_status = next(
            check for check in body["checks"] if check["name"] == "live_status"
        )
        assert body["ok"] is False
        assert live_status["ok"] is False
        assert live_status["code"] == "provider_timeout"


class TestPrinterConfig:
    def test_moonraker_config_returns_server_and_klipper_config(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with (
            patch(
                "app.services.printer_provider.MoonrakerProvider.server_info",
                new_callable=AsyncMock,
            ) as mock_server_info,
            patch(
                "app.services.printer_provider.MoonrakerProvider.info",
                new_callable=AsyncMock,
            ) as mock_printer_info,
            patch(
                "app.services.printer_provider.MoonrakerProvider.server_config",
                new_callable=AsyncMock,
            ) as mock_server_config,
            patch(
                "app.services.printer_provider.MoonrakerProvider.printer_config",
                new_callable=AsyncMock,
            ) as mock_printer_config,
        ):
            mock_server_info.return_value = {"result": {"klippy_state": "ready"}}
            mock_printer_info.return_value = {"result": {"software_version": "v1"}}
            mock_server_config.return_value = {
                "result": {"server": {"host": "0.0.0.0"}}
            }
            mock_printer_config.return_value = {
                "result": {"status": {"configfile": {"config": {"printer": {}}}}}
            }
            resp = client.get(f"/api/v1/printers/{p.id}/config", headers=auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["server_info"]["klippy_state"] == "ready"
        assert body["printer_info"]["software_version"] == "v1"
        assert body["moonraker_config"]["server"]["host"] == "0.0.0.0"
        assert "configfile" in body["klipper_config"]

    def test_config_unsupported_for_bambu(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/config", headers=auth_headers)

        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"


class TestPrinterStatus:
    def test_status_returns_printer_and_snapshot(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["printer"]["name"] == "Ender 3"
        assert data["snapshot"] == {}

    def test_status_404(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/printers/99999/status", headers=auth_headers)
        assert resp.status_code == 404


class TestPrinterJobs:
    def test_jobs_empty(self, client: TestClient, auth_headers, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/jobs", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_jobs_lists_in_order(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import File, Model

        m = Model(name="Model", slug="model", hash="i" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)

        f = File(
            model_id=m.id,
            path="/data/model.gcode",
            original_filename="model.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="j" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)

        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        job = PrintJob(
            printer_id=p.id,
            file_id=f.id,
            model_id=m.id,
            remote_filename="model.gcode",
            state=PrintJobState.COMPLETED,
            progress=1.0,
        )
        db_session.add(job)
        db_session.commit()

        resp = client.get(f"/api/v1/printers/{p.id}/jobs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["state"] == "completed"
        assert data[0]["remote_filename"] == "model.gcode"

    def test_non_superuser_cannot_list_jobs(
        self, client: TestClient, db_session: Session
    ):
        headers = _user_headers(db_session, "job-viewer")
        viewer = db_session.exec(
            select(User).where(User.username == "job-viewer")
        ).one()
        allowed = Collection(name="Allowed", slug="allowed", path="allowed")
        denied = Collection(name="Denied", slug="denied", path="denied")
        db_session.add_all([allowed, denied])
        db_session.commit()
        db_session.refresh(allowed)
        db_session.refresh(denied)
        db_session.add(
            CollectionPermission(
                user_id=viewer.id,
                collection_id=allowed.id,
                role=CollectionRole.VIEW,
            )
        )
        allowed_model = Model(
            name="Allowed job model",
            slug="allowed-job-model",
            hash="7" * 64,
            collection_id=allowed.id,
        )
        denied_model = Model(
            name="Denied job model",
            slug="denied-job-model",
            hash="8" * 64,
            collection_id=denied.id,
        )
        printer = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add_all([allowed_model, denied_model, printer])
        db_session.commit()
        db_session.refresh(allowed_model)
        db_session.refresh(denied_model)
        allowed_file = File(
            model_id=allowed_model.id,
            path="/data/allowed-job.gcode",
            original_filename="allowed-job.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="9" * 64,
        )
        denied_file = File(
            model_id=denied_model.id,
            path="/data/denied-job.gcode",
            original_filename="denied-job.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="a" * 64,
        )
        db_session.add_all([allowed_file, denied_file])
        db_session.commit()
        db_session.refresh(allowed_file)
        db_session.refresh(denied_file)
        db_session.refresh(printer)
        db_session.add_all(
            [
                PrintJob(
                    printer_id=printer.id,
                    file_id=allowed_file.id,
                    model_id=allowed_model.id,
                    remote_filename="allowed.gcode",
                    state=PrintJobState.COMPLETED,
                ),
                PrintJob(
                    printer_id=printer.id,
                    file_id=denied_file.id,
                    model_id=denied_model.id,
                    remote_filename="denied.gcode",
                    state=PrintJobState.COMPLETED,
                ),
            ]
        )
        db_session.commit()

        resp = client.get(f"/api/v1/printers/{printer.id}/jobs", headers=headers)

        assert resp.status_code == 403
        assert resp.json()["detail"] == "printer_permission_denied"


class TestPrinterFiles:
    def _setup_file(self, db_session: Session):
        from app.db.models import File, Model

        m = Model(name="Bracket", slug="bracket", hash="b" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path="/data/bracket.gcode",
            original_filename="bracket.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="c" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)
        return m, f

    def test_list_printer_files_returns_matched_and_external(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import PrinterFile

        m, f = self._setup_file(db_session)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                file_id=f.id,
                remote_filename="bracket.gcode",
                size_bytes=100,
                matched_by="filename",
            )
        )
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                remote_filename="external.gcode",
                size_bytes=200,
                matched_by="external",
            )
        )
        db_session.commit()

        resp = client.get(f"/api/v1/printers/{p.id}/files", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        matched = next(row for row in data if row["remote_filename"] == "bracket.gcode")
        assert matched["file_id"] == f.id
        assert matched["model_id"] == m.id
        assert matched["model_name"] == "Bracket"
        external = next(
            row for row in data if row["remote_filename"] == "external.gcode"
        )
        assert external["file_id"] is None

    def test_sync_printer_files_requires_auth(
        self, client: TestClient, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(f"/api/v1/printers/{p.id}/files/sync")
        assert resp.status_code == 401

    def test_sync_printer_files_uses_provider(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._setup_file(db_session)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        with patch(
            "app.services.printer_provider.MoonrakerProvider.list_files",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [{"path": "bracket.gcode", "size": 100}]
            resp = client.post(
                f"/api/v1/printers/{p.id}/files/sync",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["file_id"] == f.id
        assert data[0]["matched_by"] == "filename"

    def test_sync_printer_files_deletes_remote_files_missing_from_provider(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                remote_filename="deleted-in-mainsail.gcode",
                matched_by="external",
            )
        )
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                remote_filename="still-there.gcode",
                matched_by="external",
            )
        )
        db_session.commit()

        with patch(
            "app.services.printer_provider.MoonrakerProvider.list_files",
            new_callable=AsyncMock,
        ) as mock_list:
            mock_list.return_value = [{"path": "still-there.gcode", "size": 123}]
            resp = client.post(
                f"/api/v1/printers/{p.id}/files/sync",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert [row["remote_filename"] for row in resp.json()] == ["still-there.gcode"]
        remaining = db_session.exec(
            select(PrinterFile).where(PrinterFile.printer_id == p.id)
        ).all()
        assert [row.remote_filename for row in remaining] == ["still-there.gcode"]

    def test_delete_printer_file_removes_remote_and_resyncs_inventory(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        deleted = PrinterFile(
            printer_id=p.id,
            remote_filename="deleted.gcode",
            matched_by="external",
        )
        kept = PrinterFile(
            printer_id=p.id,
            remote_filename="kept.gcode",
            matched_by="external",
        )
        db_session.add_all([deleted, kept])
        db_session.commit()
        db_session.refresh(deleted)

        with (
            patch(
                "app.services.printer_provider.MoonrakerProvider.delete_file",
                new_callable=AsyncMock,
            ) as mock_delete,
            patch(
                "app.services.printer_provider.MoonrakerProvider.list_files",
                new_callable=AsyncMock,
            ) as mock_list,
        ):
            mock_delete.return_value = {"result": "ok"}
            mock_list.return_value = [{"path": "kept.gcode", "size": 123}]
            resp = client.delete(
                f"/api/v1/printers/{p.id}/files/{deleted.id}",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert [row["remote_filename"] for row in resp.json()] == ["kept.gcode"]
        mock_delete.assert_awaited_once_with("deleted.gcode")
        remaining = db_session.exec(
            select(PrinterFile).where(PrinterFile.printer_id == p.id)
        ).all()
        assert [row.remote_filename for row in remaining] == ["kept.gcode"]

    def test_sync_unsupported_provider(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        resp = client.post(f"/api/v1/printers/{p.id}/files/sync", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"

    def test_non_superuser_cannot_list_printer_files(
        self, client: TestClient, db_session: Session
    ):
        headers = _user_headers(db_session, "viewer")
        viewer = db_session.exec(select(User).where(User.username == "viewer")).one()
        allowed = Collection(name="Allowed", slug="allowed", path="allowed")
        denied = Collection(name="Denied", slug="denied", path="denied")
        db_session.add_all([allowed, denied])
        db_session.commit()
        db_session.refresh(allowed)
        db_session.refresh(denied)
        db_session.add(
            CollectionPermission(
                user_id=viewer.id,
                collection_id=allowed.id,
                role=CollectionRole.VIEW,
            )
        )
        allowed_model = Model(
            name="Allowed model",
            slug="allowed-model",
            hash="1" * 64,
            collection_id=allowed.id,
        )
        denied_model = Model(
            name="Denied model",
            slug="denied-model",
            hash="2" * 64,
            collection_id=denied.id,
        )
        db_session.add_all([allowed_model, denied_model])
        db_session.commit()
        db_session.refresh(allowed_model)
        db_session.refresh(denied_model)
        allowed_file = File(
            model_id=allowed_model.id,
            path="/data/allowed.gcode",
            original_filename="allowed.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="3" * 64,
        )
        denied_file = File(
            model_id=denied_model.id,
            path="/data/denied.gcode",
            original_filename="denied.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="4" * 64,
        )
        printer = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add_all([allowed_file, denied_file, printer])
        db_session.commit()
        db_session.refresh(allowed_file)
        db_session.refresh(denied_file)
        db_session.refresh(printer)
        db_session.add_all(
            [
                PrinterFile(
                    printer_id=printer.id,
                    file_id=allowed_file.id,
                    remote_filename="allowed.gcode",
                    matched_by="filename",
                ),
                PrinterFile(
                    printer_id=printer.id,
                    file_id=denied_file.id,
                    remote_filename="denied.gcode",
                    matched_by="filename",
                ),
                PrinterFile(
                    printer_id=printer.id,
                    remote_filename="external.gcode",
                    matched_by="external",
                ),
            ]
        )
        db_session.commit()

        resp = client.get(f"/api/v1/printers/{printer.id}/files", headers=headers)

        assert resp.status_code == 403
        assert resp.json()["detail"] == "printer_permission_denied"

    def test_non_superuser_cannot_send_to_printer(
        self, client: TestClient, db_session: Session
    ):
        headers = _user_headers(db_session, "editor")
        collection = Collection(name="Private", slug="private", path="private")
        db_session.add(collection)
        db_session.commit()
        db_session.refresh(collection)
        model = Model(
            name="Private model",
            slug="private-model",
            hash="5" * 64,
            collection_id=collection.id,
        )
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        file_row = File(
            model_id=model.id,
            path="/data/private.gcode",
            original_filename="private.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=100,
            sha256="6" * 64,
        )
        printer = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add_all([file_row, printer])
        db_session.commit()
        db_session.refresh(file_row)
        db_session.refresh(printer)

        resp = client.post(
            f"/api/v1/printers/{printer.id}/send",
            json={"file_id": file_row.id, "start_print": False},
            headers=headers,
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "printer_permission_denied"

    def test_start_matched_printer_file_creates_vault_job(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import PrinterFile

        m, f = self._setup_file(db_session)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                file_id=f.id,
                remote_filename="folder/bracket.gcode",
                matched_by="upload_history",
            )
        )
        db_session.commit()

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
        ) as mock_start:
            mock_start.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "folder/bracket.gcode"},
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["file_id"] == f.id
        assert data["model_id"] == m.id
        assert data["source"] == "vault"
        assert data["state"] == "started"
        mock_start.assert_awaited_once_with("folder/bracket.gcode")

    def test_start_external_printer_file_creates_external_job(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import SENTINEL_FILE_HASH, File, PrinterFile

        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        db_session.add(
            PrinterFile(
                printer_id=p.id,
                remote_filename="external.gcode",
                matched_by="external",
            )
        )
        db_session.commit()

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
        ) as mock_start:
            mock_start.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "external.gcode"},
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source"] == "external"
        sentinel_file = db_session.get(File, data["file_id"])
        assert sentinel_file is not None
        assert sentinel_file.sha256 == SENTINEL_FILE_HASH
        mock_start.assert_awaited_once_with("external.gcode")

    def test_start_bambu_provider(
        self, client: TestClient, db_session: Session, auth_headers
    ):
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.BambuLanProvider.start",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "part.gcode"},
                headers=auth_headers,
            )

        assert resp.status_code == 200


class TestSendToPrinter:
    def test_send_requires_auth(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": 1, "start_print": False},
        )
        assert resp.status_code == 401

    def test_send_non_gcode_rejected(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import File, Model

        m = Model(name="Model", slug="model-stl", hash="k" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)

        f = File(
            model_id=m.id,
            path="/data/model.stl",
            original_filename="model.stl",
            file_type="stl",
            version=1,
            size_bytes=100,
            sha256="l" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)

        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": f.id, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "file_not_gcode"

    def test_send_busy_bambu_creates_no_job(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        """The Bambu ready-state guard must run before creating a PrintJob."""
        from app.db.models import File, Model

        m = Model(name="Model", slug="model-bambu-send", hash="m" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)

        f = File(
            model_id=m.id,
            path="/data/part.gcode",
            original_filename="part.gcode",
            file_type="gcode",
            version=1,
            size_bytes=4,
            sha256="n" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)

        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new_callable=AsyncMock,
            return_value={"result": {"status": {"print_stats": {"state": "printing"}}}},
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "printer_not_ready"
        jobs = db_session.exec(
            select(PrintJob).where(PrintJob.printer_id == p.id)
        ).all()
        assert jobs == []

    def test_send_404_printer(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers/99999/send",
            json={"file_id": 1, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_send_404_file(self, client: TestClient, auth_headers, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": 99999, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_send_rejects_path_traversal_remote_filename(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/printers/1/send",
            json={"file_id": 1, "remote_filename": "../escape.gcode"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "request_validation_failed"

    def test_send_provider_crash_returns_stable_error(
        self, client: TestClient, auth_headers, db_session: Session, tmp_path
    ):
        from app.db.models import File, Model

        local = tmp_path / "bracket.gcode"
        local.write_text("G28\n")
        m = Model(name="Bracket", slug="send-crash-bracket", hash="t" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path="/data/bracket.gcode",
            original_filename="bracket.gcode",
            file_type="gcode",
            version=1,
            size_bytes=4,
            sha256="u" * 64,
        )
        db_session.add(f)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(f)
        db_session.refresh(p)

        class FakeBackend:
            def exists(self, _path):
                return True

            def download_to_path(self, _path, _target):
                return local

        with (
            patch("app.api.v1.printers.get_backend", return_value=FakeBackend()),
            patch(
                "app.services.moonraker.MoonrakerClient.upload_gcode",
                new_callable=AsyncMock,
            ) as mock_upload,
        ):
            mock_upload.side_effect = RuntimeError("secret provider stack")
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )

        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"
        assert "secret provider stack" not in resp.text
        job = db_session.exec(select(PrintJob).where(PrintJob.printer_id == p.id)).one()
        assert job.state == PrintJobState.FAILED

    def test_send_records_printer_file_inventory(
        self, client: TestClient, auth_headers, db_session: Session, tmp_path
    ):
        from app.db.models import File, Model, PrinterFile

        local = tmp_path / "bracket.gcode"
        local.write_text("G28\n")
        m = Model(name="Bracket", slug="send-bracket", hash="s" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path="/data/bracket.gcode",
            original_filename="bracket.gcode",
            file_type="gcode",
            version=1,
            size_bytes=4,
            sha256="d" * 64,
        )
        db_session.add(f)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(f)
        db_session.refresh(p)

        class FakeBackend:
            def exists(self, _path):
                return True

            def download_to_path(self, _path, _target):
                return local

        with (
            patch("app.api.v1.printers.get_backend", return_value=FakeBackend()),
            patch(
                "app.services.moonraker.MoonrakerClient.upload_gcode",
                new_callable=AsyncMock,
            ) as mock_upload,
        ):
            mock_upload.return_value = {"result": "ok"}
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json()["state"] == PrintJobState.COMPLETED.value
        row = db_session.exec(
            select(PrinterFile).where(PrinterFile.printer_id == p.id)
        ).one()
        assert row.file_id == f.id
        assert row.remote_filename == f"bracket__vault-f{f.id}-{'d' * 12}.gcode"
        assert row.matched_by == "upload_history"
        mock_upload.assert_awaited_once()
        assert mock_upload.await_args.args[1] == row.remote_filename


class TestDashboard:
    def test_dashboard_empty(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/printers/dashboard", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_printers"] == 0
        assert data["status_counts"] == {}
        assert data["active_jobs"] == 0
        assert data["groups"] == []

    def test_dashboard_with_printers(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p1 = Printer(name="P1", moonraker_url="http://10.0.0.1:7125", group="garage")
        p2 = Printer(name="P2", moonraker_url="http://10.0.0.2:7125", group="garage")
        p3 = Printer(name="P3", moonraker_url="http://10.0.0.3:7125")
        db_session.add_all([p1, p2, p3])
        db_session.commit()
        db_session.refresh(p1)
        db_session.refresh(p2)
        db_session.refresh(p3)

        from app.services.printer_hub import PrinterHub

        hub = PrinterHub()
        asyncio.run(hub._mark_status(p1.id, status="printing", error=None))
        asyncio.run(hub._mark_status(p2.id, status="ready", error=None))

        resp = client.get("/api/v1/printers/dashboard", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_printers"] == 3
        assert data["status_counts"].get("printing") == 1
        assert data["status_counts"].get("ready") == 1
        assert data["status_counts"].get("unknown") == 1
        groups = {g["name"]: g["count"] for g in data["groups"]}
        assert groups.get("garage") == 2
        assert groups.get("__ungrouped") == 1


class TestGroupFilter:
    def test_filter_by_group(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p1 = Printer(name="Prusa", moonraker_url="http://10.0.0.1:7125", group="garage")
        p2 = Printer(
            name="Ender", moonraker_url="http://10.0.0.2:7125", group="workshop"
        )
        db_session.add_all([p1, p2])
        db_session.commit()

        resp = client.get("/api/v1/printers", headers=auth_headers)
        assert len(resp.json()) == 2

        resp = client.get("/api/v1/printers?group=garage", headers=auth_headers)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Prusa"
        assert data[0]["group"] == "garage"

        resp = client.get("/api/v1/printers?group=workshop", headers=auth_headers)
        assert len(resp.json()) == 1

    def test_create_with_group(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={
                "name": "Garage Printer",
                "moonraker_url": "http://10.0.0.1:7125",
                "group": "garage",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["group"] == "garage"

    def test_update_group(self, client: TestClient, auth_headers, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(
            f"/api/v1/printers/{p.id}",
            json={"group": "workshop"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["group"] == "workshop"


class TestUpdateProviderValidation:
    """PrinterUpdate has no cross-field validator (unlike PrinterCreate), so
    switching provider on PATCH without the new provider's required fields is
    the only way to reach _validate_provider_config's 400 branches."""

    @pytest.mark.parametrize(
        "payload,expected_detail",
        [
            ({"moonraker_url": ""}, "moonraker_url_required"),
            ({"provider": "bambu_lan"}, "bambu_host_required"),
            (
                {"provider": "bambu_lan", "bambu_host": "h"},
                "bambu_serial_required",
            ),
            (
                {"provider": "bambu_lan", "bambu_host": "h", "bambu_serial": "s"},
                "bambu_access_code_required",
            ),
            ({"provider": "prusalink"}, "prusalink_url_required"),
            (
                {"provider": "prusalink", "prusalink_url": "http://p"},
                "prusalink_auth_mode_required",
            ),
            (
                {
                    "provider": "prusalink",
                    "prusalink_url": "http://p",
                    "prusalink_auth_mode": "digest",
                    "prusalink_username": "u",
                },
                "prusalink_digest_credentials_required",
            ),
            (
                {
                    "provider": "prusalink",
                    "prusalink_url": "http://p",
                    "prusalink_auth_mode": "api_key",
                },
                "prusalink_api_key_required",
            ),
            ({"provider": "elegoo_centauri"}, "elegoo_centauri_model_required"),
            (
                {
                    "provider": "elegoo_centauri",
                    "provider_variant": "elegoo_centauri_carbon",
                },
                "elegoo_centauri_host_required",
            ),
            (
                {
                    "provider": "elegoo_centauri",
                    "provider_variant": "elegoo_centauri_carbon_2",
                    "elegoo_centauri_host": "h",
                },
                "elegoo_centauri_access_code_required",
            ),
            ({"provider": "octoprint"}, "octoprint_url_required"),
            (
                {"provider": "octoprint", "octoprint_url": "http://o"},
                "octoprint_api_key_required",
            ),
        ],
    )
    def test_update_provider_validation_errors(
        self,
        client: TestClient,
        auth_headers,
        db_session: Session,
        payload,
        expected_detail,
    ):
        p = Printer(name="X", moonraker_url="http://x.local:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.patch(
            f"/api/v1/printers/{p.id}", json=payload, headers=auth_headers
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == expected_detail

    def test_update_sets_all_optional_fields(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="X", moonraker_url="http://x.local:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        payload = {
            "provider": "moonraker",
            "name": "Renamed",
            "moonraker_url": "http://renamed.local:7125",
            "api_key": "key1",
            "provider_variant": "generic",
            "bambu_host": "1.2.3.4",
            "bambu_serial": "SN1",
            "bambu_access_code": "code1",
            "prusalink_url": "http://prusa.local",
            "prusalink_auth_mode": "digest",
            "prusalink_username": "user1",
            "prusalink_password": "pass1",
            "prusalink_api_key": "key2",
            "elegoo_centauri_host": "5.6.7.8",
            "elegoo_centauri_access_code": "code2",
            "elegoo_centauri_mainboard_id": "board1",
            "octoprint_url": "http://octo.local",
            "octoprint_api_key": "key3",
            "model_name": "Model X",
            "notes": "some notes",
            "group": "lab",
        }
        resp = client.patch(
            f"/api/v1/printers/{p.id}", json=payload, headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["moonraker_url"] == "http://renamed.local:7125"
        assert data["has_api_key"] is True
        assert data["bambu_host"] == "1.2.3.4"
        assert data["bambu_serial"] == "SN1"
        assert data["prusalink_url"] == "http://prusa.local"
        assert data["prusalink_username"] == "user1"
        assert data["has_prusalink_password"] is True
        assert data["has_prusalink_api_key"] is True
        assert data["elegoo_centauri_host"] == "5.6.7.8"
        assert data["elegoo_centauri_mainboard_id"] == "board1"
        assert data["octoprint_url"] == "http://octo.local"
        assert data["has_octoprint_api_key"] is True
        assert data["model_name"] == "Model X"
        assert data["notes"] == "some notes"
        assert data["group"] == "lab"


class TestPrinterDiagnosticsExtra:
    def test_diagnostics_404_deleted_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.core.time import utcnow

        p = Printer(name="Gone", moonraker_url="http://gone.local")
        p.deleted_at = utcnow()
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"

    def test_diagnostics_configuration_error(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        # Direct DB insert bypasses the API's own _validate_provider_config,
        # simulating a row whose provider build() itself fails.
        p = Printer(
            name="Bad Elegoo",
            provider="elegoo_centauri",
            moonraker_url="",
            provider_variant="generic",
            elegoo_centauri_host="1.2.3.4",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        config_check = next(c for c in body["checks"] if c["name"] == "configuration")
        assert config_check["ok"] is False
        assert config_check["code"] == "provider_credentials_missing"

    def test_diagnostics_provider_error_check(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.info",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.get(
                f"/api/v1/printers/{p.id}/diagnostics", headers=auth_headers
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        info_check = next(c for c in body["checks"] if c["name"] == "provider_info")
        assert info_check["ok"] is False
        assert info_check["code"] == "printer_offline"


class TestPrinterConfigExtra:
    def test_config_404_deleted_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.core.time import utcnow

        p = Printer(name="Gone", moonraker_url="http://gone.local")
        p.deleted_at = utcnow()
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/config", headers=auth_headers)
        assert resp.status_code == 404

    def test_config_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.server_info",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.get(f"/api/v1/printers/{p.id}/config", headers=auth_headers)
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"


class TestSendToPrinterExtraGates:
    def _gcode_file(self, db_session: Session, suffix: str = ""):
        from app.db.models import File, Model

        m = Model(name=f"M{suffix}", slug=f"m{suffix}", hash=f"{suffix or '0'}" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path=f"/data/part{suffix}.gcode",
            original_filename=f"part{suffix}.gcode",
            file_type="gcode",
            version=1,
            size_bytes=10,
            sha256=f"{suffix or '1'}" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)
        return m, f

    def test_send_rejected_when_provider_cannot_upload(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        # Every registered provider currently supports upload, so there's no
        # real fixture for "provider without upload" — force the gate the
        # /send route actually checks (capabilities.can_upload) instead.
        from app.services.printer_provider import ElegooCentauriProvider

        _, f = self._gcode_file(db_session, "eleg")
        p = Printer(
            name="Centauri",
            provider="elegoo_centauri",
            moonraker_url="",
            provider_variant="elegoo_centauri_carbon",
            elegoo_centauri_host="192.168.1.60",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        no_upload = replace(ElegooCentauriProvider.capabilities, supported=frozenset())
        with patch.object(ElegooCentauriProvider, "capabilities", no_upload):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"

    def test_send_ready_check_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._gcode_file(db_session, "rdy")
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.BambuLanProvider.query_status",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"

    def test_send_appends_gcode_extension_when_missing(
        self, client: TestClient, auth_headers, db_session: Session, tmp_path
    ):
        _, f = self._gcode_file(db_session, "ext")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        local = tmp_path / "part.gcode"
        local.write_text("G28\n")

        class FakeBackend:
            def exists(self, _path):
                return True

            def download_to_path(self, _path, _target):
                return local

        with (
            patch("app.api.v1.printers.get_backend", return_value=FakeBackend()),
            patch(
                "app.services.moonraker.MoonrakerClient.upload_gcode",
                new_callable=AsyncMock,
                return_value={"result": "ok"},
            ),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={
                    "file_id": f.id,
                    "start_print": False,
                    "remote_filename": "no_extension",
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["remote_filename"] == "no_extension.gcode"

    def test_send_file_blob_missing_returns_410(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._gcode_file(db_session, "blob")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        class FakeBackend:
            def exists(self, _path):
                return False

        with patch("app.api.v1.printers.get_backend", return_value=FakeBackend()):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 410
        assert resp.json()["detail"] == "file_blob_missing"

    def test_send_file_role_404_when_model_deleted(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.core.time import utcnow

        m, f = self._gcode_file(db_session, "del")
        m.deleted_at = utcnow()
        db_session.add(m)
        db_session.commit()
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": f.id, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "file_not_found"

    def test_send_provider_error_marks_job_failed(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._gcode_file(db_session, "pe")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with (
            patch(
                "app.api.v1.printers.get_backend",
                return_value=type(
                    "FB", (), {"exists": staticmethod(lambda _p: True)}
                )(),
            ),
            patch(
                "app.api.v1.printers.transfer_artifact",
                new_callable=AsyncMock,
                side_effect=ProviderError("boom", code="printer_offline"),
            ),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"
        job = db_session.exec(select(PrintJob).where(PrintJob.printer_id == p.id)).one()
        assert job.state == PrintJobState.FAILED
        assert job.error == "printer_offline"

    def test_send_printer_job_error_marks_job_failed(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        _, f = self._gcode_file(db_session, "pje")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with (
            patch(
                "app.api.v1.printers.get_backend",
                return_value=type(
                    "FB", (), {"exists": staticmethod(lambda _p: True)}
                )(),
            ),
            patch(
                "app.api.v1.printers.transfer_artifact",
                new_callable=AsyncMock,
                side_effect=PrinterJobError("dispatch_failed"),
            ),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "dispatch_failed"
        job = db_session.exec(select(PrintJob).where(PrintJob.printer_id == p.id)).one()
        assert job.state == PrintJobState.FAILED
        assert job.error == "dispatch_failed"

    def test_send_http_exception_from_transfer_passes_through(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from fastapi import HTTPException

        _, f = self._gcode_file(db_session, "http")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with (
            patch(
                "app.api.v1.printers.get_backend",
                return_value=type(
                    "FB", (), {"exists": staticmethod(lambda _p: True)}
                )(),
            ),
            patch(
                "app.api.v1.printers.transfer_artifact",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=418, detail="teapot"),
            ),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/send",
                json={"file_id": f.id, "start_print": False},
                headers=auth_headers,
            )
        assert resp.status_code == 418
        assert resp.json()["detail"] == "teapot"


class TestStartPrinterFileExtra:
    def test_start_with_explicit_file_id_creates_vault_job(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import File, Model

        m = Model(name="Explicit", slug="explicit-file", hash="e" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path="/data/explicit.gcode",
            original_filename="explicit.gcode",
            file_type="gcode",
            version=1,
            size_bytes=10,
            sha256="f" * 64,
        )
        db_session.add(f)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(f)
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
            return_value={"result": "ok"},
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "explicit.gcode", "file_id": f.id},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "vault"
        assert data["file_id"] == f.id

    def test_start_with_explicit_file_id_rejects_non_gcode(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.db.models import File, Model

        m = Model(name="NonGcode", slug="non-gcode-start", hash="g" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)
        f = File(
            model_id=m.id,
            path="/data/model.stl",
            original_filename="model.stl",
            file_type="stl",
            version=1,
            size_bytes=10,
            sha256="h" * 64,
        )
        db_session.add(f)
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(f)
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/start",
            json={"remote_filename": "model.stl", "file_id": f.id},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "file_not_gcode"

    def test_start_non_superuser_sentinel_403(
        self, client: TestClient, db_session: Session
    ):
        headers = _user_headers(db_session, "start-editor")
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/start",
            json={"remote_filename": "unmatched.gcode"},
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "printer_permission_denied"

    def test_start_provider_error_marks_job_failed(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "unmatched.gcode"},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"
        job = db_session.exec(select(PrintJob).where(PrintJob.printer_id == p.id)).one()
        assert job.state == PrintJobState.FAILED

    def test_start_generic_exception_marks_job_failed(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.start",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secret stack"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/start",
                json={"remote_filename": "unmatched.gcode"},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"
        assert "secret stack" not in resp.text


class TestPrinterControlErrors:
    def test_pause_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.pause",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(f"/api/v1/printers/{p.id}/pause", headers=auth_headers)
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"

    def test_resume_generic_exception_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.resume",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secret stack"),
        ):
            resp = client.post(f"/api/v1/printers/{p.id}/resume", headers=auth_headers)
        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"
        assert "secret stack" not in resp.text

    def test_home_404_deleted_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.core.time import utcnow

        p = Printer(name="Gone", moonraker_url="http://gone.local")
        p.deleted_at = utcnow()
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(
            f"/api/v1/printers/{p.id}/home", json={}, headers=auth_headers
        )
        assert resp.status_code == 404

    def test_home_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.run_gcode",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/home", json={}, headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"

    def test_temperature_generic_exception_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.run_gcode",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secret stack"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/temperature",
                json={"heater": "bed", "target": 50},
                headers=auth_headers,
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"

    def test_emergency_stop_provider_error_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.emergency_stop",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/emergency_stop", headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"

    def test_emergency_stop_generic_exception_502(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.emergency_stop",
            new_callable=AsyncMock,
            side_effect=RuntimeError("secret stack"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/emergency_stop", headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "provider_error"


class TestPrinterFilesExtra:
    def test_sync_provider_error_sets_last_error(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.list_files",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.post(
                f"/api/v1/printers/{p.id}/files/sync", headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"
        db_session.refresh(p)
        assert p.last_error == "boom"

    def test_delete_file_unsupported_provider_409(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(
            name="Bambu",
            provider="bambu_lan",
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="access",
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.delete(f"/api/v1/printers/{p.id}/files/1", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"

    def test_delete_file_404_unknown_printer_file(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.delete(
            f"/api/v1/printers/{p.id}/files/99999", headers=auth_headers
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_file_not_found"

    def test_delete_file_provider_error_sets_last_error(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        row = PrinterFile(
            printer_id=p.id, remote_filename="stuck.gcode", matched_by="external"
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)

        with patch(
            "app.services.printer_provider.MoonrakerProvider.delete_file",
            new_callable=AsyncMock,
            side_effect=ProviderError("boom", code="printer_offline"),
        ):
            resp = client.delete(
                f"/api/v1/printers/{p.id}/files/{row.id}", headers=auth_headers
            )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "printer_offline"
        db_session.refresh(p)
        assert p.last_error == "boom"

    def test_delete_file_falls_back_to_cached_list_when_resync_fails(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        deleted = PrinterFile(
            printer_id=p.id, remote_filename="deleted.gcode", matched_by="external"
        )
        kept = PrinterFile(
            printer_id=p.id, remote_filename="kept.gcode", matched_by="external"
        )
        db_session.add_all([deleted, kept])
        db_session.commit()
        db_session.refresh(deleted)

        with (
            patch(
                "app.services.printer_provider.MoonrakerProvider.delete_file",
                new_callable=AsyncMock,
                return_value={"result": "ok"},
            ),
            patch(
                "app.services.printer_provider.MoonrakerProvider.list_files",
                new_callable=AsyncMock,
                side_effect=ProviderError("boom", code="printer_offline"),
            ),
        ):
            resp = client.delete(
                f"/api/v1/printers/{p.id}/files/{deleted.id}", headers=auth_headers
            )
        assert resp.status_code == 200
        assert [row["remote_filename"] for row in resp.json()] == ["kept.gcode"]


class TestWsTicketExtra:
    def test_ws_ticket_404_unknown_printer(self, client: TestClient, auth_headers):
        resp = client.post("/api/v1/printers/99999/ws-ticket", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "printer_not_found"

    def test_ws_ticket_404_deleted_printer(
        self, client: TestClient, auth_headers, db_session: Session
    ):
        from app.core.time import utcnow

        p = Printer(name="Gone", moonraker_url="http://gone.local")
        p.deleted_at = utcnow()
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.post(f"/api/v1/printers/{p.id}/ws-ticket", headers=auth_headers)
        assert resp.status_code == 404


class TestPrinterWebSocketBearerToken:
    def test_bearer_header_token_authenticates(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ):
        printer = Printer(name="Bearer", moonraker_url="http://printer.local")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        with client.websocket_connect(
            f"/api/v1/printers/{printer.id}/ws", headers=auth_headers
        ):
            pass

    def test_bearer_header_invalid_token_closes(
        self, client: TestClient, db_session: Session
    ):
        printer = Printer(name="BadToken", moonraker_url="http://printer.local")
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/printers/{printer.id}/ws",
                headers={"Authorization": "Bearer not-a-real-token"},
            ):
                pass
