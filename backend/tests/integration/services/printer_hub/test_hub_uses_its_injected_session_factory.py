"""Defends hub uses its injected session factory at the services printer hub integration boundary.

A regression could persist stale printer status or complete the wrong active job.
"""

from __future__ import annotations

from ._printer_hub_shared import (
    AsyncMock,
    InProcessBus,
    MagicMock,
    MaterialSlotState,
    MaterialSource,
    Printer,
    PrinterHub,
    PrinterMaterialSlot,
    PrinterProvider,
    PrinterStatus,
    PrinterTool,
    PrintJob,
    PrintJobState,
    SimpleNamespace,
    SpoolmanError,
    _gcode,
    asyncio,
    get_session_factory,
    patch,
    printer_hub_module,
    pytest,
    select,
)


def test_hub_uses_its_injected_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifecycle database access stays behind the construction seam."""
    factory = get_session_factory()
    hub = PrinterHub(InProcessBus(), session_factory=factory)

    def _unexpected_global_lookup():
        raise AssertionError("global session lookup")

    monkeypatch.setattr(
        printer_hub_module,
        "get_session_factory",
        _unexpected_global_lookup,
    )

    asyncio.run(hub.start_all())


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
            patch.object(
                hub, "_spoolman_config", return_value=("http://spoolman", None)
            ),
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
    assert failed.artifact_capture_error_code == "external_artifact_capture_disabled"
    assert failed.artifact_capture_error_message

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
            hub._capture_external_artifact(1, job.id, "/cache/external.gcode", client)
        )
    db_session.expire_all()
    failed = db_session.get(PrintJob, job.id)
    assert failed is not None
    assert failed.artifact_capture_error == "download_failed"
    assert failed.artifact_capture_error_code == "download_failed"
    assert failed.artifact_capture_error_message

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
        asyncio.run(
            hub._capture_external_artifact(1, 2, "/cache/external.gcode", client)
        )


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

            hub._provider_builder = lambda _printer: FakeClient()
            with (
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

            hub._provider_builder = lambda _printer: FlakyClient()
            with (
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
