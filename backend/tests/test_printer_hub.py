"""Tests for PrinterHub background worker."""

from __future__ import annotations

import asyncio

import pytest

from app.db.models import Printer, PrinterStatus, PrintJob, PrintJobState


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
        assert len(jobs) == 1, "reconnect after a mid-print drop must not duplicate the job"


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

        monkeypatch.setattr(
            "app.services.printer_hub.asyncio.to_thread", failing_sync
        )

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
