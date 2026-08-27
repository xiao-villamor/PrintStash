"""Defends printer hub sync active job at the services printer hub integration boundary.

A regression could persist stale printer status or complete the wrong active job.
"""

from __future__ import annotations

from ._printer_hub_shared import (
    InProcessBus,
    Printer,
    PrinterHub,
    PrinterProvider,
    PrintJob,
    PrintJobState,
    ThreadPoolExecutor,
    asyncio,
    get_session_factory,
    pytest,
    select,
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

    def test_identityless_report_reuses_active_vault_job_by_filename(
        self, hub, db_session
    ):
        """Provider-neutral status must not create a duplicate external row."""
        pid, job = self._setup_job(db_session)

        def _sync() -> None:
            hub._sync_active_job_db(
                pid,
                "printing",
                "sync.gcode",
                0.45,
                {"state": "printing", "filename": "sync.gcode"},
            )
            hub._sync_active_job_db(
                pid,
                "complete",
                "sync.gcode",
                1.0,
                {"state": "complete", "filename": "sync.gcode"},
            )

        _sync()

        with get_session_factory().session() as session:
            rows = session.exec(
                select(PrintJob).where(
                    PrintJob.printer_id == pid,
                    PrintJob.remote_filename == "sync.gcode",
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].id == job.id
            assert rows[0].source == "vault"
            assert rows[0].state == PrintJobState.COMPLETED
            assert rows[0].progress == pytest.approx(1.0)
            assert rows[0].finished_at is not None

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

    def test_bambu_project_only_then_task_only_reuses_one_job(self, hub):
        from sqlmodel import select

        from app.db.session import get_session_factory

        hub._sync_active_job_db(
            1,
            "printing",
            "plate_1.gcode",
            0.1,
            {
                "state": "printing",
                "filename": "plate_1.gcode",
                "external_project_id": "project-transition",
            },
        )
        hub._sync_active_job_db(
            1,
            "printing",
            "plate_1.gcode",
            0.2,
            {
                "state": "printing",
                "filename": "plate_1.gcode",
                "external_task_id": "task-transition",
            },
        )

        with get_session_factory().session() as session:
            rows = session.exec(select(PrintJob).where(PrintJob.printer_id == 1)).all()
            assert len(rows) == 1
            assert rows[0].external_project_id == "project-transition"
            assert rows[0].external_task_id == "task-transition"
            assert rows[0].provider_job_id == "task-transition"

    def test_bambu_cross_field_identity_equality_does_not_merge(self, hub):
        from sqlmodel import select

        from app.db.session import get_session_factory

        hub._sync_active_job_db(
            1,
            "printing",
            "same-file.gcode",
            0.1,
            {
                "state": "printing",
                "filename": "same-file.gcode",
                "external_project_id": "same-id",
            },
        )
        hub._sync_active_job_db(
            1,
            "printing",
            "same-file.gcode",
            0.2,
            {
                "state": "printing",
                "filename": "same-file.gcode",
                "external_task_id": "same-id",
            },
        )

        with get_session_factory().session() as session:
            rows = session.exec(
                select(PrintJob).where(PrintJob.printer_id == 1).order_by(PrintJob.id)
            ).all()
            assert len(rows) == 2
            assert rows[0].external_project_id == "same-id"
            assert rows[0].external_task_id is None
            assert rows[1].external_task_id == "same-id"
            assert rows[1].external_project_id is None

    def test_bambu_conflicting_typed_identity_does_not_merge(self, hub):
        from sqlmodel import select

        from app.db.session import get_session_factory

        hub._sync_active_job_db(
            1,
            "printing",
            "conflict.gcode",
            0.1,
            {
                "state": "printing",
                "filename": "conflict.gcode",
                "external_task_id": "task-stable",
                "external_project_id": "project-old",
            },
        )
        hub._sync_active_job_db(
            1,
            "printing",
            "conflict.gcode",
            0.2,
            {
                "state": "printing",
                "filename": "conflict.gcode",
                "external_task_id": "task-stable",
                "external_project_id": "project-new",
            },
        )

        with get_session_factory().session() as session:
            rows = session.exec(
                select(PrintJob).where(PrintJob.printer_id == 1).order_by(PrintJob.id)
            ).all()
            assert len(rows) == 2
            assert rows[0].external_task_id == "task-stable"
            assert rows[0].external_project_id == "project-old"
            assert rows[1].external_task_id == "task-stable"
            assert rows[1].external_project_id == "project-new"

    def test_identityless_bambu_report_does_not_merge_typed_external_job(
        self, hub, db_session
    ):
        printer = Printer(
            name="Bambu identity guard",
            provider=PrinterProvider.BAMBU_LAN,
            bambu_host="192.0.2.11",
            bambu_serial="TEST-SERIAL-IDENTITY",
            bambu_access_code="test-code",
        )
        db_session.add(printer)
        db_session.commit()
        db_session.refresh(printer)
        assert printer.id is not None

        hub._sync_active_job_db(
            printer.id,
            "printing",
            "identity.gcode",
            0.1,
            {
                "state": "printing",
                "filename": "identity.gcode",
                "external_task_id": "task-identity",
            },
        )
        hub._sync_active_job_db(
            printer.id,
            "printing",
            "identity.gcode",
            0.2,
            {"state": "printing", "filename": "identity.gcode"},
        )

        with get_session_factory().session() as session:
            rows = session.exec(
                select(PrintJob)
                .where(PrintJob.printer_id == printer.id)
                .order_by(PrintJob.id)
            ).all()
            assert len(rows) == 2
            assert rows[0].external_task_id == "task-identity"
            assert rows[1].external_task_id is None

    def test_concurrent_bambu_initial_callbacks_create_one_job(self, threaded_hub_db):
        from app.db.session import get_session_factory, override_session_factory

        factory = get_session_factory()
        hub = PrinterHub(InProcessBus(), session_factory=factory)
        with get_session_factory().session() as session:
            session.add(
                Printer(
                    name="Concurrent Bambu",
                    provider=PrinterProvider.BAMBU_LAN,
                    bambu_host="192.0.2.10",
                    bambu_serial="TEST-SERIAL",
                    bambu_access_code="test-code",
                )
            )
            session.commit()

        reports = [
            {
                "state": "printing",
                "filename": "concurrent.gcode",
                "external_project_id": "project-concurrent",
            },
            {
                "state": "printing",
                "filename": "concurrent.gcode",
                "external_task_id": "task-concurrent",
            },
        ]

        def reconcile(report):
            # ContextVars do not cross raw ThreadPoolExecutor workers; mirror
            # the app's asyncio.to_thread propagation explicitly in this unit.
            override_session_factory(factory)
            return hub._sync_active_job_db(
                1, "printing", "concurrent.gcode", 0.1, report
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reconcile, reports))
        assert results == [None, None]

        with get_session_factory().session() as session:
            rows = session.exec(select(PrintJob).where(PrintJob.printer_id == 1)).all()
            assert len(rows) == 1
            assert rows[0].external_project_id == "project-concurrent"
            assert rows[0].external_task_id == "task-concurrent"

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
