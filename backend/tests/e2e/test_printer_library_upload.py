"""A saved Revision reaches a disposable Moonraker through the upload-only API."""

import pytest

from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import a_gcode_artifact, build_printer
from tests.fakes.mock_printer import create_app
from tests.fakes.server import start_server


@pytest.fixture
def bambu_cache(e2e_db, monkeypatch):
    from app.modules.printing.bambu_adapter import BambuLanProvider
    from tests.fakes.mock_bambu import FakeFtpTls, make_mqtt_factory
    from tests.fakes.print_sim import PrintSim

    sim = PrintSim(total_mm=10, total_seconds=10, print_seconds=1)
    mqtt, clients = make_mqtt_factory(sim)
    ftp = FakeFtpTls(files={"cache/Slicer/External project.3mf": b"zip"})
    monkeypatch.setattr(BambuLanProvider, "_default_mqtt_client", staticmethod(mqtt))
    monkeypatch.setattr(BambuLanProvider, "_ftps_client", lambda self: ftp)
    printer = build_printer(
        e2e_db,
        provider="bambu_lan",
        bambu_host="127.0.0.1",
        bambu_serial="TEST",
        bambu_access_code="test-code",
    )
    return printer, ftp, clients, sim


class TestUploadFromLibrary:
    @pytest.mark.asyncio
    async def test_upload_only_exposes_the_confirmed_file_without_starting_the_printer(
        self, api, superuser_headers, e2e_db
    ) -> None:
        controller, state = create_app()
        running = start_server(controller)
        try:
            printer = build_printer(e2e_db, moonraker_url=running.base_url)
            revision = a_gcode_artifact(e2e_db, "transfer-only")
            get_backend().write_bytes(b"G28\n", revision.path)
            path = f"/api/v1/printers/{printer.id}"

            sent = await api.post(
                f"{path}/send",
                headers=superuser_headers,
                json={"file_id": revision.id, "start_print": False},
            )
            assert sent.status_code == 200, sent.text
            receipt = sent.json()
            assert receipt["state"] == "completed"
            assert receipt["started_at"] is None
            assert state.file_contents[receipt["remote_filename"]] == b"G28\n"
            assert not state.sim.is_active()

            files = await api.get(f"{path}/files", headers=superuser_headers)
            assert files.status_code == 200, files.text
            assert files.json()[0]["remote_filename"] == receipt["remote_filename"]
            assert files.json()[0]["matched_by"] == "upload_history"
        finally:
            running.stop()

    @pytest.mark.asyncio
    async def test_bambu_sync_reconciles_a_library_upload_in_a_populated_cache(
        self, api, superuser_headers, e2e_db, bambu_cache
    ) -> None:
        printer, ftp, _, sim = bambu_cache
        revision = a_gcode_artifact(e2e_db, "bambu-cache-transfer")
        get_backend().write_bytes(b"G28\n", revision.path)
        path = f"/api/v1/printers/{printer.id}"
        sent = await api.post(
            f"{path}/send",
            headers=superuser_headers,
            json={"file_id": revision.id, "start_print": False},
        )
        assert sent.status_code == 200, sent.text
        name = sent.json()["remote_filename"]
        assert ftp.files[f"cache/{name}"] == b"G28\n"
        assert sent.json()["started_at"] is None

        synced = await api.post(f"{path}/files/sync", headers=superuser_headers)
        assert synced.status_code == 200, synced.text
        files = await api.get(f"{path}/files", headers=superuser_headers)
        assert files.status_code == 200, files.text
        rows = {row["remote_filename"]: row for row in files.json()}
        assert set(rows) == {name, "Slicer/External project.3mf"}
        assert rows[name]["file_id"] == revision.id
        assert rows[name]["size_bytes"] == 4
        assert rows["Slicer/External project.3mf"]["size_bytes"] == 3
        assert sim.state == "standby"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", ["send", "files", "queue"])
    async def test_bambu_native_project_starts_only_the_chosen_plate(
        self, api, superuser_headers, e2e_db, bambu_cache, route
    ):
        from app.modules.printing.bambu_adapter import BambuLanProvider
        from app.modules.printing.printer_jobs import dispatch_next
        from tests.fixtures.three_mf_projects import build_sliced_project

        printer, ftp, clients, _ = bambu_cache
        rev = a_gcode_artifact(e2e_db, "Native project", dispatchable=True)
        rev.original_filename = "native.gcode.3mf"
        archive = build_sliced_project()
        get_backend().write_bytes(archive, rev.path)
        e2e_db.add(rev)
        e2e_db.commit()
        path = f"/api/v1/printers/{printer.id}"
        payload = {"file_id": rev.id, "plate_index": 2}
        if route == "queue":
            queued = await api.post(
                "/api/v1/fleet/queue",
                headers=superuser_headers,
                json={**payload, "strategy": "manual", "printer_id": printer.id},
            )
            assert queued.status_code == 201, queued.text
            name = queued.json()["remote_filename"]
            assert await dispatch_next(BambuLanProvider.build) == queued.json()["id"]
        else:
            sent = await api.post(
                f"{path}/send",
                headers=superuser_headers,
                json={**payload, "start_print": route == "send"},
            )
            assert sent.status_code == 200, sent.text
            name = sent.json()["remote_filename"]
        assert name.endswith(".3mf")
        assert ftp.files[f"cache/{name}"] == archive
        if route == "files":
            assert not any(
                "print" in msg for client in clients for msg in client.published
            )
            synced = await api.post(f"{path}/files/sync", headers=superuser_headers)
            assert synced.status_code == 200
            row = next(row for row in synced.json() if row["remote_filename"] == name)
            assert row["plate_index"] == 2
            unknown = await api.post(
                f"{path}/start",
                headers=superuser_headers,
                json={
                    "remote_filename": "Slicer/External project.3mf",
                    "file_id": rev.id,
                },
            )
            assert unknown.status_code == 409
            started = await api.post(
                f"{path}/start",
                headers=superuser_headers,
                json={"remote_filename": name},
            )
            assert started.status_code == 200, started.text
        commands = [
            msg["print"]
            for client in clients
            for msg in client.published
            if "print" in msg
        ]
        assert len(commands) == 1
        assert commands[0]["command"] == "project_file"
        assert commands[0]["param"] == "Metadata/plate_2.gcode"
        assert commands[0]["url"] == f"file:///sdcard/cache/{name}"
        assert commands[0]["subtask_id"].isdigit()


class TestBambuReconnect:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("interrupted_starts", "first_report", "expect_original"),
        [
            pytest.param(1, "RUNNING", True, id="unique-running"),
            pytest.param(1, "PAUSE", True, id="unique-paused"),
            pytest.param(2, "RUNNING", False, id="ambiguous"),
            pytest.param(1, "FINISH", False, id="terminal-only"),
        ],
    )
    async def test_first_identified_report_after_hub_restart_preserves_run_identity(
        self,
        api,
        superuser_headers,
        e2e_db,
        bambu_cache,
        interrupted_starts,
        first_report,
        expect_original,
        monkeypatch,
    ):
        """A recent local start needs an identified active report to own the outcome."""
        from app.main import app
        from app.modules.printing.bambu_adapter import BambuLanProvider
        from app.modules.printing.printer_hub import PrinterHub
        from tests.fakes.mock_bambu import make_mqtt_factory

        printer, _, _, sim = bambu_cache
        revision = a_gcode_artifact(e2e_db, "restart-receipt")
        get_backend().write_bytes(b"G28\n", revision.path)
        path = f"/api/v1/printers/{printer.id}"
        upload = await api.post(
            f"{path}/send",
            headers=superuser_headers,
            json={"file_id": revision.id, "start_print": False},
        )
        assert upload.status_code == 200, upload.text
        name = upload.json()["remote_filename"]
        starts = set()
        for _ in range(interrupted_starts):
            if starts:
                # The device stopped the first attempt while its observer was offline.
                sim.cancel()
            start = await api.post(
                f"{path}/start",
                headers=superuser_headers,
                json={"remote_filename": name, "file_id": revision.id},
            )
            assert start.status_code == 200, start.text
            starts.add(start.json()["id"])
        assert sim.filename == name
        assert len(starts) == interrupted_starts

        # Recreate process-local observer state, retaining the application DB.
        # The public MQTT double replays the printer's first identified report.
        hub = PrinterHub()
        monkeypatch.setattr(app.state, "printer_hub", hub)
        packet = {
            "print": {
                "subtask_id": "device-run-42",
                "gcode_file": f"/cache/{name}",
                "subtask_name": name,
            }
        }
        mqtt, _ = make_mqtt_factory(sim, pushall_report=packet)
        monkeypatch.setattr(
            BambuLanProvider, "_default_mqtt_client", staticmethod(mqtt)
        )
        provider = BambuLanProvider.build(printer)
        for state in (first_report, "FINISH", "FINISH"):
            packet["print"].update(
                gcode_state=state, mc_percent=100 if state == "FINISH" else 40
            )
            status = await provider.query_status()
            await hub._handle_status(printer.id, status["result"]["status"])

        history = await api.get(f"{path}/jobs", headers=superuser_headers)
        assert history.status_code == 200, history.text
        runs = [row for row in history.json() if row["id"] != upload.json()["id"]]
        completed = [row for row in runs if row["state"] == "completed"]
        assert len(completed) == 1
        assert completed[0]["external_subtask_id"] == "device-run-42"
        assert completed[0]["finished_at"] is not None
        queue = await api.get(
            "/api/v1/fleet/queue",
            headers=superuser_headers,
            params={"history_limit": 0},
        )
        assert queue.status_code == 200, queue.text
        active = {row["id"] for row in queue.json() if row["printer_id"] == printer.id}
        if expect_original:
            assert len(runs) == 1
            assert completed[0]["id"] in starts
            assert completed[0]["file_id"] == revision.id
            assert active == set()
        else:
            assert len(runs) == len(starts) + 1
            assert completed[0]["source"] == "external"
            assert completed[0]["id"] not in starts
            assert active == starts
