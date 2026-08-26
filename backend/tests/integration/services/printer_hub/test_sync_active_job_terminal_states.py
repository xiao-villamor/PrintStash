"""Defends sync active job terminal states at the services printer hub integration boundary.

A regression could persist stale printer status or complete the wrong active job.
"""

from __future__ import annotations

from ._printer_hub_shared import (
    Printer,
    PrintJob,
    PrintJobState,
    asyncio,
    pytest,
)


class TestPrinterHubSyncActiveJob:
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

        def _tick(state, progress, stats):
            hub._sync_active_job_db(7, state, "repeat.gcode", progress, stats)

        # First external print: start -> complete.
        _tick("printing", 0.5, {"state": "printing"})
        _tick("complete", 1.0, {"state": "complete", "total_duration": 100})
        # Second external print of the same file begins.
        _tick("printing", 0.1, {"state": "printing"})

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

        def _tick(state, stats):
            hub._sync_active_job_db(8, state, "once.gcode", 1.0, stats)

        _tick("printing", {"state": "printing"})
        _tick("complete", {"state": "complete", "total_duration": 50})
        _tick("complete", {"state": "complete", "total_duration": 50})

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

    @pytest.mark.parametrize(
        ("terminal_state", "expected_state"),
        [
            ("complete", PrintJobState.COMPLETED),
            ("cancelled", PrintJobState.CANCELLED),
            ("error", PrintJobState.FAILED),
        ],
    )
    def test_first_terminal_snapshot_never_leaves_a_phantom_active_job(
        self, hub, terminal_state, expected_state
    ):
        """The initial row must become terminal in the same transaction."""
        from sqlmodel import select

        from app.db.models import PrintJob
        from app.db.session import get_session_factory
        from app.services.fleet import fleet_summary

        asyncio.run(
            hub._sync_active_job(
                1,
                terminal_state,
                "stale-terminal.gcode",
                0.0,
                {"state": terminal_state},
            )
        )

        with get_session_factory().session() as session:
            job = session.exec(
                select(PrintJob).where(
                    PrintJob.remote_filename == "stale-terminal.gcode"
                )
            ).one()
            assert job.state == expected_state
            assert job.finished_at is not None
            assert fleet_summary(session)["active_jobs"] == 0

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
