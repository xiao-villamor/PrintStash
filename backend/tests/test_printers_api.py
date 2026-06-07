"""Tests for Printers API router (FastAPI TestClient)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import PrintJob, PrintJobState, Printer, PrinterStatus


class TestListPrinters:
    def test_list_empty(self, client: TestClient):
        resp = client.get("/api/v1/printers")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_printer(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get("/api/v1/printers")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Ender 3"
        assert data[0]["status"] == PrinterStatus.UNKNOWN.value


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


class TestGetPrinter:
    def test_get_returns_printer(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Ender 3"

    def test_get_404(self, client: TestClient):
        resp = client.get("/api/v1/printers/99999")
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

        resp2 = client.get(f"/api/v1/printers/{p.id}")
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
        assert body["capabilities"]["can_upload"] is False
        assert body["capabilities"]["can_pause"] is True
        assert body["capabilities"]["support_level"] == "beta"
        assert "send" in body["capabilities"]["unsupported_actions"]

    def test_create_bambu_missing_fields_422(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/printers",
            json={"name": "Bambu", "provider": "bambu_lan"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_bambu_send_rejected(
        self, client: TestClient, db_session: Session, auth_headers
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

        resp = client.post(
            f"/api/v1/printers/{p.id}/send",
            json={"file_id": f.id, "start_print": False},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"

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

    def test_bambu_diagnostics_reports_beta_without_send_parity(
        self, client: TestClient, db_session: Session
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
            resp = client.get(f"/api/v1/printers/{p.id}/diagnostics")

        assert resp.status_code == 200
        body = resp.json()
        assert body["support_level"] == "beta"
        assert "send" in body["unsupported_actions"]
        assert body["ok"] is True
        assert [check["name"] for check in body["checks"]] == [
            "configuration",
            "provider_info",
            "live_status",
        ]

    def test_diagnostics_timeout_returns_check_failure(
        self, client: TestClient, db_session: Session, monkeypatch
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
            resp = client.get(f"/api/v1/printers/{p.id}/diagnostics")

        assert resp.status_code == 200
        body = resp.json()
        live_status = next(
            check for check in body["checks"] if check["name"] == "live_status"
        )
        assert body["ok"] is False
        assert live_status["ok"] is False
        assert live_status["code"] == "provider_timeout"


class TestPrinterStatus:
    def test_status_returns_printer_and_snapshot(
        self, client: TestClient, db_session: Session
    ):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["printer"]["name"] == "Ender 3"
        assert data["snapshot"] == {}

    def test_status_404(self, client: TestClient):
        resp = client.get("/api/v1/printers/99999/status")
        assert resp.status_code == 404


class TestPrinterJobs:
    def test_jobs_empty(self, client: TestClient, db_session: Session):
        p = Printer(name="Ender 3", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        resp = client.get(f"/api/v1/printers/{p.id}/jobs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_jobs_lists_in_order(self, client: TestClient, db_session: Session):
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

        resp = client.get(f"/api/v1/printers/{p.id}/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["state"] == "completed"
        assert data[0]["remote_filename"] == "model.gcode"


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
        self, client: TestClient, db_session: Session
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

        resp = client.get(f"/api/v1/printers/{p.id}/files")
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
        from app.db.models import File, SENTINEL_FILE_HASH, PrinterFile

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

    def test_start_unsupported_provider(
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

        resp = client.post(
            f"/api/v1/printers/{p.id}/start",
            json={"remote_filename": "part.gcode"},
            headers=auth_headers,
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "operation_not_supported_for_provider"


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
        row = db_session.exec(
            select(PrinterFile).where(PrinterFile.printer_id == p.id)
        ).one()
        assert row.file_id == f.id
        assert row.remote_filename == "bracket.gcode"
        assert row.matched_by == "upload_history"


class TestDashboard:
    def test_dashboard_empty(self, client: TestClient):
        resp = client.get("/api/v1/printers/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_printers"] == 0
        assert data["status_counts"] == {}
        assert data["active_jobs"] == 0
        assert data["groups"] == []

    def test_dashboard_with_printers(self, client: TestClient, db_session: Session):
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
        hub._mark_status(p1.id, status="printing", error=None)
        hub._mark_status(p2.id, status="ready", error=None)

        resp = client.get("/api/v1/printers/dashboard")
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
    def test_filter_by_group(self, client: TestClient, db_session: Session):
        p1 = Printer(name="Prusa", moonraker_url="http://10.0.0.1:7125", group="garage")
        p2 = Printer(
            name="Ender", moonraker_url="http://10.0.0.2:7125", group="workshop"
        )
        db_session.add_all([p1, p2])
        db_session.commit()

        resp = client.get("/api/v1/printers")
        assert len(resp.json()) == 2

        resp = client.get("/api/v1/printers?group=garage")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Prusa"
        assert data[0]["group"] == "garage"

        resp = client.get("/api/v1/printers?group=workshop")
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
