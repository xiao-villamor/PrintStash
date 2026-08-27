"""Defends bambu printer operations at the printers API integration boundary.

A regression could authorize or issue a printer operation against the wrong device.
"""

from __future__ import annotations

from ._printers_shared import (
    AsyncMock,
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    Printer,
    PrintJob,
    PrintJobState,
    Session,
    TestClient,
    User,
    _user_headers,
    asyncio,
    patch,
    select,
)


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
