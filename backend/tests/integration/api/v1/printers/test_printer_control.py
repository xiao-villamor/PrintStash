"""Defends printer control at the printers API integration boundary.

A regression could authorize or issue a printer operation against the wrong device.
"""

from __future__ import annotations

from ._printers_shared import (
    AsyncMock,
    File,
    FileType,
    Model,
    Printer,
    PrinterProvider,
    PrintJob,
    PrintJobState,
    Session,
    TestClient,
    patch,
    select,
)


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
