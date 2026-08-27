"""Defends start and websocket at the printers API integration boundary.

A regression could authorize or issue a printer operation against the wrong device.
"""

from __future__ import annotations

from ._printers_shared import (
    AsyncMock,
    Printer,
    PrinterFile,
    PrintJob,
    PrintJobState,
    ProviderError,
    Session,
    TestClient,
    WebSocketDisconnect,
    _user_headers,
    patch,
    pytest,
    select,
)


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
