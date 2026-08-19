"""Tests for PrinterHub background worker."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import select

from app.db.models import (
    MaterialSlotState,
    MaterialSource,
    Printer,
    PrinterMaterialSlot,
    PrinterProvider,
    PrinterStatus,
    PrinterTool,
    PrintJob,
    PrintJobState,
)
from app.services import printer_hub as printer_hub_module
from app.services.printer_hub import PrinterHub
from app.services.spoolman import SpoolmanError
from tests.test_fleet_api import _gcode


def test_provider_material_state_sync_creates_updates_and_removes_rows(
    hub: PrinterHub, db_session
) -> None:
    printer = Printer(
        name="AMS",
        provider=PrinterProvider.BAMBU_LAN,
        host="192.0.2.50",
        serial="TEST-SERIAL",
        access_code="test-code",
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    assert printer.id is not None

    hub._sync_material_state_db(
        printer.id,
        [
            {},
            {
                "slot_key": "ams:0:0",
                "label": "Tray 1",
                "tool_key": "tool0",
                "state": "loaded",
                "material_type": " PLA ",
                "material_brand": " Brand ",
                "color_hex": "aabbccdd",
                "spool_id": 11,
                "spool_name": "PLA spool",
                "spool_filament_id": 22,
            },
            {
                "slot_key": "ams:0:1",
                "state": "invalid",
                "color_hex": "not-a-color",
                "spool_id": "not-an-int",
                "spool_filament_id": False,
            },
        ],
        tools=[
            None,
            {},
            {"tool_key": "tool0", "label": "Nozzle", "nozzle_diameter_mm": 0.4},
            {"tool_key": "tool1", "nozzle_diameter_mm": True},
        ],
    )

    db_session.expire_all()
    slots = db_session.exec(
        select(PrinterMaterialSlot).where(
            PrinterMaterialSlot.printer_id == printer.id,
            PrinterMaterialSlot.source == MaterialSource.BAMBU_AMS,
        )
    ).all()
    assert [(row.slot_key, row.state) for row in slots] == [
        ("ams:0:0", MaterialSlotState.LOADED),
        ("ams:0:1", MaterialSlotState.UNKNOWN),
    ]
    assert slots[0].material_type == "PLA"
    assert slots[0].material_brand == "Brand"
    assert slots[0].color_hex == "#AABBCC"
    assert slots[1].color_hex is None
    assert slots[1].spool_id is None
    tools = db_session.exec(
        select(PrinterTool).where(
            PrinterTool.printer_id == printer.id,
            PrinterTool.source == MaterialSource.BAMBU_AMS,
        )
    ).all()
    assert [row.nozzle_diameter_mm for row in tools] == [0.4, None]

    hub._sync_material_state_db(
        printer.id,
        [{"slot_key": "ams:0:0", "label": "Updated", "state": "empty"}],
        tools=[{"tool_key": "tool0", "label": "Updated", "nozzle_diameter_mm": -1}],
    )
    db_session.expire_all()
    slots = db_session.exec(
        select(PrinterMaterialSlot).where(
            PrinterMaterialSlot.printer_id == printer.id,
            PrinterMaterialSlot.source == MaterialSource.BAMBU_AMS,
        )
    ).all()
    assert len(slots) == 1
    assert slots[0].label == "Updated"
    assert slots[0].state == MaterialSlotState.EMPTY
    tools = db_session.exec(
        select(PrinterTool).where(
            PrinterTool.printer_id == printer.id,
            PrinterTool.source == MaterialSource.BAMBU_AMS,
        )
    ).all()
    assert len(tools) == 1
    assert tools[0].nozzle_diameter_mm is None

    printer.provider_material_sync_enabled = False
    db_session.add(printer)
    db_session.commit()
    hub._sync_material_state_db(printer.id, [{"slot_key": "ignored"}])
    hub._sync_material_state_db(999_999, [{"slot_key": "ignored"}])


def test_material_slot_enrichment_resolves_inventory_and_degrades_cleanly(
    hub: PrinterHub,
) -> None:
    slots = [
        {"slot_key": "tool0", "external_spool_id": 7},
        {"slot_key": "tool1", "external_spool_id": 8},
        {"slot_key": "manual"},
    ]
    resolved = {
        "name": "Red PLA",
        "filament": {
            "id": 70,
            "material": "PLA",
            "color_hex": "FF0000",
            "vendor": {"name": "Example"},
        },
    }

    async def run() -> list[dict[str, object]]:
        async def get_spool(spool_id: int) -> dict[str, object]:
            if spool_id == 7:
                return resolved
            raise SpoolmanError("missing")

        with (
            patch.object(hub, "_spoolman_config", return_value=("http://spoolman", None)),
            patch(
                "app.services.printer_hub.SpoolmanClient.get_spool",
                new=AsyncMock(side_effect=get_spool),
            ),
        ):
            return await hub._enrich_material_slots(1, slots)

    enriched = asyncio.run(run())
    assert enriched[0] == {
        "slot_key": "tool0",
        "external_spool_id": 7,
        "material_type": "PLA",
        "material_brand": "Example",
        "color_hex": "FF0000",
        "spool_id": 7,
        "spool_name": "Red PLA",
        "spool_filament_id": 70,
    }
    assert "material_type" not in enriched[1]

    with patch.object(hub, "_spoolman_config", return_value=None):
        assert asyncio.run(hub._enrich_material_slots(1, slots)) == slots
    assert asyncio.run(hub._enrich_material_slots(1, [{"slot_key": "manual"}])) == [
        {"slot_key": "manual"}
    ]


def test_hub_material_config_helpers_and_snapshot_attach(hub: PrinterHub) -> None:
    assert printer_hub_module._reported_int("bad") is None
    assert printer_hub_module._reported_float(object()) is None

    with patch.object(
        printer_hub_module.runtime_config, "spoolman_enabled", return_value=False
    ):
        assert hub._spoolman_config() is None
    with (
        patch.object(
            printer_hub_module.runtime_config, "spoolman_enabled", return_value=True
        ),
        patch.object(
            printer_hub_module.runtime_config,
            "spoolman_config",
            return_value={"base_url": "", "api_key": None},
        ),
    ):
        assert hub._spoolman_config() is None
    with (
        patch.object(
            printer_hub_module.runtime_config, "spoolman_enabled", return_value=True
        ),
        patch.object(
            printer_hub_module.runtime_config,
            "spoolman_config",
            return_value={"base_url": "http://spoolman", "api_key": "secret"},
        ),
    ):
        assert hub._spoolman_config() == ("http://spoolman", "secret")

    websocket = MagicMock()
    websocket.send_json = AsyncMock()
    hub.snapshots[3] = {"print_stats": {"state": "ready"}}
    asyncio.run(hub.attach(3, websocket))
    websocket.send_json.assert_awaited_once()
    websocket.send_json.side_effect = RuntimeError("disconnected")
    asyncio.run(hub.attach(3, websocket))
    asyncio.run(hub.detach(3, websocket))


def test_external_capture_failure_paths_are_persistent(
    hub: PrinterHub, db_session
) -> None:
    artifact = _gcode(db_session)
    job = PrintJob(
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="external.gcode",
        source="external",
        state=PrintJobState.PRINTING,
        artifact_evidence="capture_pending",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    assert job.id is not None

    with patch.object(
        printer_hub_module,
        "settings",
        SimpleNamespace(bambu_external_capture_max_mb=0),
    ):
        asyncio.run(
            hub._capture_external_artifact(
                1, job.id, "/cache/external.gcode", MagicMock()
            )
        )
    db_session.expire_all()
    failed = db_session.get(PrintJob, job.id)
    assert failed is not None
    assert failed.artifact_capture_error == "external_artifact_capture_disabled"

    failed.artifact_evidence = "capture_pending"
    db_session.add(failed)
    db_session.commit()

    class DownloadError(RuntimeError):
        code = "download_failed"

    client = MagicMock()
    client.download_artifact = AsyncMock(side_effect=DownloadError())
    with patch.object(
        printer_hub_module,
        "settings",
        SimpleNamespace(bambu_external_capture_max_mb=1),
    ):
        asyncio.run(
            hub._capture_external_artifact(
                1, job.id, "/cache/external.gcode", client
            )
        )
    db_session.expire_all()
    failed = db_session.get(PrintJob, job.id)
    assert failed is not None
    assert failed.artifact_capture_error == "download_failed"

    hub._mark_capture_failed(999_999, "ignored")
    failed.artifact_evidence = "metadata_only"
    db_session.add(failed)
    db_session.commit()
    hub._mark_capture_failed(job.id, "ignored")


def test_external_capture_success_and_cancellation_paths(hub: PrinterHub) -> None:
    client = MagicMock()

    async def download(_remote: str, staged, *, max_bytes: int) -> None:
        assert max_bytes > 0
        staged.write_bytes(b"; generated")

    client.download_artifact = AsyncMock(side_effect=download)
    with (
        patch.object(
            printer_hub_module,
            "settings",
            SimpleNamespace(bambu_external_capture_max_mb=1),
        ),
        patch.object(hub, "_persist_external_artifact") as persist,
    ):
        asyncio.run(
            hub._capture_external_artifact(1, 2, "/cache/external.gcode", client)
        )
        persist.assert_called_once()

    client.download_artifact = AsyncMock(side_effect=asyncio.CancelledError())
    with (
        patch.object(
            printer_hub_module,
            "settings",
            SimpleNamespace(bambu_external_capture_max_mb=1),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(hub._capture_external_artifact(1, 2, "/cache/external.gcode", client))


class TestPrinterHubLifecycle:
    def test_init_creates_empty_collections(self, hub):
        assert hub.snapshots == {}
        assert hub.bus is not None
        assert hub.tasks == {}
        assert hub.stop_events == {}

    def test_add_printer_creates_task(self, hub, db_session):
        p = Printer(name="Test", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        async def _run():
            await hub.add_printer(p.id)

        asyncio.run(_run())
        assert p.id in hub.tasks
        asyncio.run(hub.remove_printer(p.id))
        assert p.id not in hub.tasks

    def test_remove_printer_cleans_up(self, hub, db_session):
        p = Printer(name="Test", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        async def _add():
            await hub.add_printer(p.id)

        asyncio.run(_add())
        assert p.id in hub.tasks

        async def _remove():
            await hub.remove_printer(p.id)

        asyncio.run(_remove())
        assert p.id not in hub.tasks
        assert p.id not in hub.stop_events
        assert p.id not in hub.snapshots

    def test_add_printer_is_idempotent(self, hub, db_session):
        p = Printer(name="Test", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        async def _add():
            await hub.add_printer(p.id)
            await hub.add_printer(p.id)

        asyncio.run(_add())
        assert p.id in hub.tasks
        asyncio.run(hub.remove_printer(p.id))

    def test_run_printer_marks_offline_on_initial_query_failure(self, hub, db_session):
        from unittest.mock import patch

        p = Printer(name="Test", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        stop = asyncio.Event()

        class FakeClient:
            async def query_status(self):
                raise RuntimeError("query blocked")

            async def subscribe_status(self, _on_status, *, stop_event=None):
                return None

        async def _run():
            async def _sleep(_seconds: float) -> None:
                stop.set()

            with (
                patch(
                    "app.services.printer_hub.get_provider_client",
                    return_value=FakeClient(),
                ),
                patch("app.services.printer_hub.asyncio.sleep", side_effect=_sleep),
            ):
                await hub._run_printer(p.id, stop)

        asyncio.run(_run())
        db_session.refresh(p)
        assert p.status == PrinterStatus.OFFLINE
        assert p.last_error is not None


class TestPrinterHubChaosReconnect:
    """Simulate the Wi-Fi-flap / dropped-socket / reboot-mid-print scenario:
    the transport dies mid-print, the worker backs off and reconnects, and
    the printer must recover to its live state without duplicating the job."""

    def test_reconnect_after_socket_drop_mid_print_recovers_without_duplicate_job(
        self, hub, db_session
    ):
        from unittest.mock import patch

        p = Printer(name="Chaos", moonraker_url="http://10.0.0.5:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        stop = asyncio.Event()

        printing_status = {
            "print_stats": {"state": "printing", "filename": "chaos.gcode"},
            "virtual_sdcard": {"progress": 0.3},
        }
        attempts = {"n": 0}

        class FlakyClient:
            async def query_status(self):
                return {"result": {"status": printing_status}}

            async def subscribe_status(self, on_status, *, stop_event=None):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    # One good tick, then the socket dies mid-print (Wi-Fi
                    # flap / reboot both surface here as a dead transport).
                    await on_status(printing_status)
                    raise ConnectionError("socket dropped mid-print")
                # Reconnect succeeds; printer resumes reporting live state.
                await on_status(printing_status)
                stop.set()

        sleep_calls: list[float] = []

        async def _run():
            async def _sleep(seconds: float) -> None:
                sleep_calls.append(seconds)

            with (
                patch(
                    "app.services.printer_hub.get_provider_client",
                    return_value=FlakyClient(),
                ),
                patch("app.services.printer_hub.asyncio.sleep", side_effect=_sleep),
            ):
                await hub._run_printer(p.id, stop)

        asyncio.run(_run())

        db_session.refresh(p)
        assert p.status == PrinterStatus.PRINTING, "must recover, not stay offline"
        assert attempts["n"] == 2, "worker must reconnect after the dropped socket"
        assert sleep_calls == [1.0], "backoff must fire once for the one drop"

        from sqlmodel import select

        jobs = db_session.exec(
            select(PrintJob).where(PrintJob.remote_filename == "chaos.gcode")
        ).all()
        assert len(jobs) == 1, (
            "reconnect after a mid-print drop must not duplicate the job"
        )


class TestPrinterHubMarkStatus:
    def test_mark_status_updates_db(self, hub, db_session):
        p = Printer(name="Test", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        pid = p.id

        asyncio.run(hub._mark_status(pid, PrinterStatus.PRINTING, error="nozzle clog"))
        db_session.refresh(p)
        assert p.status == PrinterStatus.PRINTING

    def test_mark_status_clears_error(self, hub, db_session):
        p = Printer(
            name="Test", moonraker_url="http://10.0.0.1:7125", last_error="old error"
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        pid = p.id

        asyncio.run(hub._mark_status(pid, PrinterStatus.READY, error=None))
        db_session.refresh(p)
        assert p.status == PrinterStatus.READY
        assert p.last_error is None

    def test_mark_status_handles_missing_printer(self, hub):
        asyncio.run(hub._mark_status(99999, PrinterStatus.OFFLINE, error="gone"))


class TestPrinterHubHandleStatus:
    def test_handle_status_merges_snapshot(self, hub):
        status = {
            "print_stats": {"state": "printing", "filename": "test.gcode"},
            "virtual_sdcard": {"progress": 0.25, "file_size": 1234},
        }

        async def _run():
            await hub._handle_status(1, status)

        asyncio.run(_run())
        snap = hub.snapshots.get(1, {})
        assert snap["print_stats"]["state"] == "printing"
        assert snap["virtual_sdcard"]["progress"] == 0.25

    def test_handle_status_updates_existing(self, hub):
        hub.snapshots[1] = {
            "print_stats": {"state": "printing", "filename": "old.gcode"},
            "virtual_sdcard": {"progress": 0.10},
        }
        status = {"virtual_sdcard": {"progress": 0.50}}

        async def _run():
            await hub._handle_status(1, status)

        asyncio.run(_run())
        snap = hub.snapshots[1]
        assert snap["print_stats"]["state"] == "printing"
        assert snap["virtual_sdcard"]["progress"] == 0.50

    def test_handle_status_skips_non_dict_fields(self, hub):
        status = {
            "print_stats": "not a dict",
            "virtual_sdcard": {"progress": 0.99},
        }

        async def _run():
            await hub._handle_status(1, status)

        asyncio.run(_run())
        snap = hub.snapshots.get(1, {})
        assert "print_stats" not in snap
        assert "virtual_sdcard" in snap


class TestStateMapping:
    def test_state_map_values(self):
        from app.services.printer_hub import _STATE_MAP, _WEBHOOK_STATE_MAP

        assert _STATE_MAP["standby"] == PrinterStatus.READY
        assert _STATE_MAP["printing"] == PrinterStatus.PRINTING
        assert _STATE_MAP["paused"] == PrinterStatus.PAUSED
        assert _STATE_MAP["error"] == PrinterStatus.ERROR
        assert _STATE_MAP["shutdown"] == PrinterStatus.OFFLINE
        assert _STATE_MAP["complete"] == PrinterStatus.READY
        assert _STATE_MAP["cancelled"] == PrinterStatus.READY
        assert _STATE_MAP["running"] == PrinterStatus.PRINTING
        assert _STATE_MAP["idle"] == PrinterStatus.READY
        assert _WEBHOOK_STATE_MAP["ready"] == PrinterStatus.READY
        assert _WEBHOOK_STATE_MAP["shutdown"] == PrinterStatus.OFFLINE
        assert _WEBHOOK_STATE_MAP["error"] == PrinterStatus.ERROR

    def test_derive_status_uses_webhook_state_when_print_stats_missing(self):
        from app.services.printer_hub import _derive_printer_status

        status = {
            "webhooks": {"state": "ready", "state_message": "Printer is ready"},
            "virtual_sdcard": {"progress": 0.0},
        }
        ms_state, vault_status = _derive_printer_status(status)
        assert ms_state == "ready"
        assert vault_status == PrinterStatus.READY

    def test_derive_status_print_stats_takes_precedence(self):
        from app.services.printer_hub import _derive_printer_status

        status = {
            "print_stats": {"state": "printing"},
            "webhooks": {"state": "ready"},
        }
        assert _derive_printer_status(status) == ("printing", PrinterStatus.PRINTING)

    def test_derive_status_unknown_state_maps_to_unknown(self):
        from app.services.printer_hub import _derive_printer_status

        ms_state, vault_status = _derive_printer_status(
            {"print_stats": {"state": "warming_up"}}
        )
        assert ms_state == "warming_up"
        assert vault_status == PrinterStatus.UNKNOWN

    def test_derive_status_empty_snapshot_is_unknown(self):
        from app.services.printer_hub import _derive_printer_status

        assert _derive_printer_status({}) == ("", PrinterStatus.UNKNOWN)


class TestPrinterHubSyncActiveJob:
    def test_sync_circuit_breaker_bounds_repeated_failures(self, hub, monkeypatch):
        calls = 0

        async def failing_sync(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("database unavailable")

        monkeypatch.setattr("app.services.printer_hub.asyncio.to_thread", failing_sync)

        async def _run():
            for _ in range(4):
                await hub._sync_active_job(1, "printing", "cube.gcode", 0.5, {})

        asyncio.run(_run())
        failures, retry_after = hub._job_sync_breakers[1]
        assert calls == 3
        assert failures == 3
        assert retry_after > 0

    def test_sync_progress_is_coalesced(self, hub, monkeypatch):
        calls = 0

        async def successful_sync(*_args, **_kwargs):
            nonlocal calls
            calls += 1

        monkeypatch.setattr(
            "app.services.printer_hub.asyncio.to_thread", successful_sync
        )

        async def _run():
            await hub._sync_active_job(1, "printing", "cube.gcode", 0.5, {})
            await hub._sync_active_job(1, "printing", "cube.gcode", 0.9, {})

        asyncio.run(_run())
        assert calls == 1

    def _setup_job(self, db_session):
        from app.db.models import File, Model

        m = Model(name="SyncTest", slug="sync-test", hash="m" * 64)
        db_session.add(m)
        db_session.commit()
        db_session.refresh(m)

        f = File(
            model_id=m.id,
            path="/data/sync.gcode",
            original_filename="sync.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="n" * 64,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)

        p = Printer(name="SyncTest", moonraker_url="http://10.0.0.1:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        job = PrintJob(
            printer_id=p.id,
            file_id=f.id,
            model_id=m.id,
            remote_filename="sync.gcode",
            state=PrintJobState.STARTED,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return p.id, job

    def test_sync_updates_state_and_progress(self, hub, db_session):
        pid, job = self._setup_job(db_session)

        async def _sync():
            await hub._sync_active_job(
                pid,
                "printing",
                "sync.gcode",
                0.45,
                {"state": "printing", "filename": "sync.gcode"},
            )

        asyncio.run(_sync())
        db_session.refresh(job)
        assert job.state == PrintJobState.PRINTING
        assert job.progress == pytest.approx(0.45)

    def test_sync_complete_sets_finished_at(self, hub, db_session):
        pid, job = self._setup_job(db_session)

        async def _sync():
            await hub._sync_active_job(
                pid,
                "complete",
                "sync.gcode",
                1.0,
                {"state": "complete", "filename": "sync.gcode"},
            )

        asyncio.run(_sync())
        db_session.refresh(job)
        assert job.state == PrintJobState.COMPLETED
        assert job.finished_at is not None

    def test_sync_no_filename_returns_early(self, hub):
        async def _sync():
            await hub._sync_active_job(1, "printing", None, 0.0, {})

        asyncio.run(_sync())

    def test_sync_no_matching_row(self, hub):
        """With printing state and no matching row, an external job is auto-created."""
        from sqlmodel import select

        from app.db.models import PrintJob, PrintJobState

        async def _sync():
            await hub._sync_active_job(
                1, "printing", "ext-test.gcode", 0.5, {"state": "printing"}
            )

        asyncio.run(_sync())
        # Verify the external job was created
        from app.db.session import get_session_factory

        with get_session_factory().session() as session:
            job = session.exec(
                select(PrintJob).where(
                    PrintJob.printer_id == 1,
                    PrintJob.remote_filename == "ext-test.gcode",
                )
            ).first()
            assert job is not None
            assert job.source == "external"
            assert job.state == PrintJobState.PRINTING
            assert job.artifact_evidence == "metadata_only"

    def test_external_bambu_job_preserves_reported_identity(self, hub):
        from sqlmodel import select

        from app.db.session import get_session_factory

        stats = {
            "state": "printing",
            "filename": "plate_1.gcode",
            "external_display_name": "Benchy",
            "external_task_id": "task-42",
            "external_subtask_id": "subtask-7",
            "external_project_id": "project-3",
            "external_profile_id": "profile-2",
            "external_gcode_file": "plate_1.gcode",
            "external_plate_index": 1,
            "external_current_layer": 8,
            "external_total_layers": 120,
            "external_nozzle_diameter": 0.4,
        }

        asyncio.run(hub._sync_active_job(1, "printing", "plate_1.gcode", 0.1, stats))

        with get_session_factory().session() as session:
            job = session.exec(
                select(PrintJob).where(PrintJob.provider_job_id == "task-42")
            ).one()
            assert job.remote_filename == "plate_1.gcode"
            assert job.external_display_name == "Benchy"
            assert job.external_subtask_id == "subtask-7"
            assert job.external_project_id == "project-3"
            assert job.external_profile_id == "profile-2"
            assert job.external_current_layer == 8
            assert job.external_total_layers == 120
            assert job.external_nozzle_diameter == pytest.approx(0.4)

    def test_external_bambu_gcode_is_archived_when_cache_is_available(self, hub):
        from sqlmodel import select

        from app.db.models import SENTINEL_MODEL_HASH, Model
        from app.db.session import get_session_factory

        class CaptureClient:
            async def download_artifact(self, remote_path, local_path, *, max_bytes):
                assert remote_path == "/cache/benchy.gcode"
                assert max_bytes > 0
                local_path.write_bytes(b"; generated by Bambu Studio\nG28\n")

        async def _run():
            await hub._handle_status(
                1,
                {
                    "print_stats": {
                        "state": "printing",
                        "filename": "benchy.gcode",
                        "external_display_name": "Benchy",
                        "external_task_id": "capture-task",
                        "external_gcode_file": "/cache/benchy.gcode",
                    },
                    "virtual_sdcard": {"progress": 0.2},
                },
                client=CaptureClient(),
            )
            await asyncio.gather(*list(hub._capture_tasks.values()))

        asyncio.run(_run())

        with get_session_factory().session() as session:
            job = session.exec(
                select(PrintJob).where(PrintJob.provider_job_id == "capture-task")
            ).one()
            model = session.get(Model, job.model_id)
            assert job.artifact_evidence == "gcode_archived"
            assert job.artifact_capture_error is None
            assert model is not None
            assert model.hash != SENTINEL_MODEL_HASH

    def test_sentinel_rows_are_created_lazily(self, db_session):
        from sqlmodel import select

        from app.db.models import (
            SENTINEL_FILE_HASH,
            SENTINEL_MODEL_HASH,
            File,
            Model,
        )
        from app.services.printer_hub import _get_sentinel_ids

        sentinel_file = db_session.exec(
            select(File).where(File.sha256 == SENTINEL_FILE_HASH)
        ).first()
        if sentinel_file is not None:
            db_session.delete(sentinel_file)
            db_session.commit()

        sentinel_model = db_session.exec(
            select(Model).where(Model.hash == SENTINEL_MODEL_HASH)
        ).first()
        if sentinel_model is not None:
            db_session.delete(sentinel_model)
            db_session.commit()

        file_id, model_id = _get_sentinel_ids(db_session)

        assert file_id is not None
        assert model_id is not None
        assert db_session.get(File, file_id) is not None
        assert db_session.get(Model, model_id) is not None

    def test_external_reprint_creates_new_job(self, hub):
        """A second external print of the same file must not revive the first
        (now-finished) job — it should create a fresh history row."""
        from sqlmodel import select

        from app.db.models import PrintJob, PrintJobState
        from app.db.session import get_session_factory

        async def _tick(state, progress, stats):
            await hub._sync_active_job(7, state, "repeat.gcode", progress, stats)

        # First external print: start -> complete.
        asyncio.run(_tick("printing", 0.5, {"state": "printing"}))
        asyncio.run(
            _tick("complete", 1.0, {"state": "complete", "total_duration": 100})
        )
        # Second external print of the same file begins.
        asyncio.run(_tick("printing", 0.1, {"state": "printing"}))

        with get_session_factory().session() as session:
            jobs = session.exec(
                select(PrintJob)
                .where(PrintJob.remote_filename == "repeat.gcode")
                .order_by(PrintJob.created_at.asc())  # type: ignore[attr-defined]
            ).all()
        assert len(jobs) == 2, "second print should create a new job, not revive"
        assert jobs[0].state == PrintJobState.COMPLETED  # first run preserved
        assert jobs[0].finished_at is not None
        assert jobs[1].state == PrintJobState.PRINTING  # new run

    def test_active_job_cache_is_used_on_repeat_tick(self, hub, db_session):
        """After the first tick, a same-filename tick hits the cache: the
        expensive filtered-select lookup runs at most once, not per tick."""
        import app.services.printer_hub as printer_hub_mod

        pid, job = self._setup_job(db_session)

        select_calls = 0
        real_select = printer_hub_mod.select

        def _counting_select(*args, **kwargs):
            nonlocal select_calls
            select_calls += 1
            return real_select(*args, **kwargs)

        async def _tick(state, stats):
            await hub._sync_active_job(pid, state, "sync.gcode", 0.1, stats)

        asyncio.run(_tick("printing", {"state": "printing"}))
        assert hub._active_job_cache[pid] == ("sync.gcode", job.id)

        printer_hub_mod.select = _counting_select
        try:
            asyncio.run(_tick("printing", {"state": "printing"}))
        finally:
            printer_hub_mod.select = real_select

        assert select_calls == 0, "cache hit should skip the PrintJob select"

    def test_repeated_complete_tick_does_not_duplicate(self, hub):
        """A second 'complete' tick after a print finishes is idempotent — it
        must match the existing finished row, not create a duplicate."""
        from sqlmodel import select

        from app.db.models import PrintJob
        from app.db.session import get_session_factory

        async def _tick(state, stats):
            await hub._sync_active_job(8, state, "once.gcode", 1.0, stats)

        asyncio.run(_tick("printing", {"state": "printing"}))
        asyncio.run(_tick("complete", {"state": "complete", "total_duration": 50}))
        asyncio.run(_tick("complete", {"state": "complete", "total_duration": 50}))

        with get_session_factory().session() as session:
            jobs = session.exec(
                select(PrintJob).where(PrintJob.remote_filename == "once.gcode")
            ).all()
        assert len(jobs) == 1

    def test_sync_no_matching_row_standby_ignored(self, hub):
        """Standby state with no matching row should NOT create a job."""
        from sqlmodel import select

        from app.db.models import PrintJob

        async def _sync():
            await hub._sync_active_job(
                1, "standby", "standby.gcode", 0.0, {"state": "standby"}
            )

        asyncio.run(_sync())
        from app.db.session import get_session_factory

        with get_session_factory().session() as session:
            job = session.exec(
                select(PrintJob).where(PrintJob.remote_filename == "standby.gcode")
            ).first()
            assert job is None

    def test_sync_sets_error_on_failure(self, hub, db_session):
        pid, job = self._setup_job(db_session)

        async def _sync():
            await hub._sync_active_job(
                pid,
                "error",
                "sync.gcode",
                0.10,
                {
                    "state": "error",
                    "filename": "sync.gcode",
                    "message": "thermal runaway",
                },
            )

        asyncio.run(_sync())
        db_session.refresh(job)
        assert job.state == PrintJobState.FAILED
        assert job.error == "thermal runaway"


class TestGetHubDependency:
    def test_get_hub_from_app_state(self, hub, monkeypatch):
        """get_hub FastAPI dependency resolves hub from app.state."""
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient

        from app.services.printer_hub import get_hub

        test_app = FastAPI()
        test_app.state.printer_hub = hub

        @test_app.get("/test")
        def endpoint(h=Depends(get_hub)):
            return {"type": type(h).__name__}

        tc = TestClient(test_app)
        resp = tc.get("/test")
        assert resp.json()["type"] == "PrinterHub"
