"""Defends diagnostics errors at the printers API integration boundary.

A regression could authorize or issue a printer operation against the wrong device.
"""

from __future__ import annotations

from ._printers_shared import (
    AsyncMock,
    Printer,
    PrinterJobError,
    PrintJob,
    PrintJobState,
    ProviderError,
    Session,
    TestClient,
    patch,
    replace,
    select,
)


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

        with (
            patch(
                "app.services.printer_provider.MoonrakerProvider.info",
                new_callable=AsyncMock,
                side_effect=ProviderError("boom", code="printer_offline"),
            ),
            patch(
                "app.services.printer_provider.MoonrakerProvider.query_status",
                new_callable=AsyncMock,
                return_value={},
            ),
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
