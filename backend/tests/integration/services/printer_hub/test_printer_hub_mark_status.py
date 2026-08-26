"""Defends printer hub mark status at the services printer hub integration boundary.

A regression could persist stale printer status or complete the wrong active job.
"""

from __future__ import annotations

from ._printer_hub_shared import (
    Printer,
    PrinterStatus,
    asyncio,
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
