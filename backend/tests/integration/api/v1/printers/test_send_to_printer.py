"""Defends send to printer at the printers API integration boundary.

A regression could authorize or issue a printer operation against the wrong device.
"""

from __future__ import annotations

from ._printers_shared import (
    AsyncMock,
    Printer,
    PrintJob,
    PrintJobState,
    Session,
    TestClient,
    asyncio,
    patch,
    pytest,
    select,
)


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
        ids=[
            "moonraker-url",
            "bambu-host",
            "bambu-serial",
            "bambu-access-code",
            "prusalink-url",
            "prusalink-auth-mode",
            "prusalink-digest-credentials",
            "prusalink-api-key",
            "centauri-model",
            "centauri-host",
            "centauri-access-code",
            "octoprint-url",
            "octoprint-api-key",
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
