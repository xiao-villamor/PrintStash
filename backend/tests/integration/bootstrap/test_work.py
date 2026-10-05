"""Composing background work for one process: catalog, engine, lifecycle.

Every role builds the same catalog and binds one engine; what differs is
which lanes a process executes. A split topology (a worker, or an API that
does not run jobs) is refused unless every process can reach the same
database and the same storage, because otherwise work would run against a
vault only one process can see.

Startup is where recovery happens: executions another application version
left behind are cancelled, queued-pass marks a dead process left are
forgotten, and every definition is reconciled once, so whatever the database
says is owed is resubmitted without waiting for the first tick.
"""

from __future__ import annotations

import time

import pytest
from sqlmodel import Session

import app.bootstrap.work as work_bootstrap
from app.core.config import ProcessRole, _overlay, settings
from app.core.time import utcnow
from app.db.models import (
    Job,
    JobKind,
    JobState,
    LaneName,
    ReconcileCursor,
    WorkExecutor,
    WorkLaneOverride,
    WorkPriority,
)
from app.modules.work import catalog as catalog_module
from app.modules.work import executors, fences
from app.modules.work.catalog import WorkCatalog
from app.modules.work.contracts import (
    Deduplicated,
    EngineStatus,
    JobSubmission,
    PassSubmission,
)
from app.modules.work.jobs import jobs

POSTGRES = "postgresql://printstash:secret@db/printstash"
# Every test binds the inline engine through this seam; these tests need the
# real one, captured before any fixture replaced it.
_BUILD_ENGINE = work_bootstrap.build_engine


def _passes(engine) -> set[JobKind]:
    return {
        execution.submission.source
        for execution in engine.executions.values()
        if isinstance(execution.submission, PassSubmission)
    }


class TestDefinitions:
    def test_collects_every_owners_definitions_once(self) -> None:
        names = [definition.name for definition in work_bootstrap.definitions()]

        assert len(names) == len(set(names))
        assert {
            JobKind.WORK_HOUSEKEEPING,
            JobKind.INGESTION_UPLOAD,
            JobKind.DERIVATIVES_MESH,
            JobKind.NOTIFICATIONS_DELIVER,
            JobKind.ADMINISTRATION_AUDIT,
            JobKind.BACKUPS_CREATE,
            JobKind.PRINTING_DISPATCH,
        } <= set(names)

    def test_every_kind_has_exactly_one_definition(self) -> None:
        # A member no definition answers to is a dead name the database would
        # still accept; a definition outside the enum cannot exist at all.
        names = [definition.name for definition in work_bootstrap.definitions()]

        assert sorted(names) == sorted(JobKind)

    def test_includes_similarity_when_its_package_is_installed(self) -> None:
        names = {definition.name for definition in work_bootstrap.definitions()}

        assert JobKind.SIMILARITY_ANALYZE in names

    def test_leaves_similarity_out_when_its_package_is_absent(
        self, monkeypatch
    ) -> None:
        import app.bootstrap.optional_features as optional

        monkeypatch.setattr(optional, "similarity_available", lambda: False)

        names = {definition.name for definition in work_bootstrap.definitions()}

        assert not any(name.startswith("similarity.") for name in names)


class TestBuildCatalog:
    def test_applies_an_administrators_lane_override(self, db_session: Session) -> None:
        db_session.add(WorkLaneOverride(lane=LaneName.INGEST, concurrency=7))
        db_session.commit()

        catalog = work_bootstrap.build_catalog()

        assert catalog.lanes[LaneName.INGEST].concurrency == 7

    def test_an_unreadable_override_table_stops_startup(self, monkeypatch) -> None:
        # Silently running at configured concurrency would ignore what an
        # administrator set; startup reports the broken database instead.
        def unreadable(self, _session):
            raise RuntimeError("override table missing")

        monkeypatch.setattr(WorkCatalog, "apply_overrides", unreadable)

        with pytest.raises(RuntimeError, match="override table missing"):
            work_bootstrap.build_catalog()


class TestListenLanes:
    @pytest.mark.parametrize(
        ("role", "runs_jobs", "expected"),
        [
            (ProcessRole.ALL, True, None),
            (ProcessRole.API, True, None),
            (ProcessRole.API, False, []),
            (ProcessRole.WORKER, True, None),
        ],
    )
    def test_listens_by_role(
        self, role: ProcessRole, runs_jobs: bool, expected
    ) -> None:
        _overlay["process_role"] = role
        _overlay["api_runs_jobs"] = runs_jobs

        assert work_bootstrap.listen_lanes() == expected


class TestValidateTopology:
    @pytest.mark.parametrize(
        ("role", "runs_jobs"),
        [(ProcessRole.ALL, True), (ProcessRole.API, True)],
        ids=["all", "api"],
    )
    def test_a_single_process_role_runs_on_sqlite(
        self, role: ProcessRole, runs_jobs: bool
    ) -> None:
        _overlay["process_role"] = role
        _overlay["api_runs_jobs"] = runs_jobs
        _overlay["db_url"] = "sqlite:////data/db/printstash.sqlite"

        work_bootstrap.validate_topology()

    @pytest.mark.parametrize(
        ("role", "runs_jobs"),
        [(ProcessRole.WORKER, True), (ProcessRole.API, False)],
        ids=["worker", "api-without-jobs"],
    )
    def test_a_split_topology_refuses_sqlite(
        self, role: ProcessRole, runs_jobs: bool
    ) -> None:
        _overlay["process_role"] = role
        _overlay["api_runs_jobs"] = runs_jobs
        _overlay["db_url"] = "sqlite:////data/db/printstash.sqlite"

        with pytest.raises(RuntimeError, match="requires PostgreSQL"):
            work_bootstrap.validate_topology()

    def test_a_split_topology_refuses_an_undeclared_local_volume(self) -> None:
        _overlay["process_role"] = ProcessRole.WORKER
        _overlay["db_url"] = POSTGRES
        _overlay["storage_backend"] = "local"
        _overlay["shared_storage"] = False

        with pytest.raises(RuntimeError, match="VAULT_SHARED_STORAGE"):
            work_bootstrap.validate_topology()

    def test_a_split_topology_accepts_a_declared_shared_volume(self) -> None:
        _overlay["process_role"] = ProcessRole.WORKER
        _overlay["db_url"] = POSTGRES
        _overlay["storage_backend"] = "local"
        _overlay["shared_storage"] = True

        work_bootstrap.validate_topology()

    def test_object_storage_still_needs_a_shared_staging_volume(self) -> None:
        # Uploads are staged on local disk whatever the storage backend; the
        # worker that commits one reads the bytes the API staged.
        _overlay["process_role"] = ProcessRole.WORKER
        _overlay["db_url"] = POSTGRES
        _overlay["storage_backend"] = "s3"
        _overlay["shared_storage"] = False

        with pytest.raises(RuntimeError, match="staging"):
            work_bootstrap.validate_topology()

    def test_a_split_topology_accepts_object_storage_with_shared_staging(
        self,
    ) -> None:
        _overlay["process_role"] = ProcessRole.WORKER
        _overlay["db_url"] = POSTGRES
        _overlay["storage_backend"] = "s3"
        _overlay["shared_storage"] = True

        work_bootstrap.validate_topology()


class TestBuildEngine:
    def test_keeps_engine_state_beside_a_sqlite_vault(self, work_catalog) -> None:
        from app.runtime.engine.dbos_engine import DbosJobEngine

        _overlay["db_url"] = "sqlite:////data/db/printstash.sqlite"

        engine = _BUILD_ENGINE(work_catalog)

        assert isinstance(engine, DbosJobEngine)
        assert engine.url == "sqlite:////data/db/printstash-dbos.sqlite"
        assert engine.schema is None
        assert engine.executor_id == executors.executor_id()

    def test_keeps_engine_state_in_its_own_postgres_schema(self, work_catalog) -> None:
        _overlay["db_url"] = POSTGRES

        engine = _BUILD_ENGINE(work_catalog)

        assert engine.schema == "dbos"  # type: ignore[attr-defined]
        assert engine.url.startswith("postgresql+psycopg://")  # type: ignore[attr-defined]


@pytest.fixture
def started(work_engine, work_catalog):
    runtime = work_bootstrap.start(engine=work_engine, catalog=work_catalog)
    try:
        yield runtime
    finally:
        work_bootstrap.stop()


class TestStart:
    def test_makes_this_process_a_registered_executor(
        self, started, work_engine, db_session: Session
    ) -> None:
        assert work_bootstrap.current() is started
        assert catalog_module.get_engine() is work_engine
        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None
        assert row.role == settings.process_role
        assert set(row.lanes.split(",")) == set(started.catalog.lanes)

    def test_reconciles_every_definition_once(self, started, work_engine) -> None:
        assert _passes(work_engine) == set(started.catalog.definitions)

    def test_startup_passes_are_backfill(self, started, work_engine) -> None:
        # So an upload arriving during startup is not queued behind them.
        priorities = {
            execution.submission.priority
            for execution in work_engine.executions.values()
            if isinstance(execution.submission, PassSubmission)
        }

        assert priorities == {WorkPriority.BACKFILL}

    def test_a_pass_a_dead_process_left_queued_does_not_suppress_startup(
        self, work_engine, work_catalog, db_session: Session
    ) -> None:
        # The mark says "a pass is already waiting", but the process that
        # queued it died, so that pass never runs.
        db_session.add(
            ReconcileCursor(
                source=JobKind.SOURCES_SCAN,
                pass_queued_at=utcnow(),
                pass_priority=WorkPriority.INTERACTIVE,
            )
        )
        db_session.commit()

        work_bootstrap.start(engine=work_engine, catalog=work_catalog)
        try:
            assert JobKind.SOURCES_SCAN in _passes(work_engine)
        finally:
            work_bootstrap.stop()

    def test_the_vaults_api_reruns_its_predecessors_work(
        self, work_engine, work_catalog, make_work_executor
    ) -> None:
        previous = make_work_executor("previous-api", role="all")

        work_bootstrap.start(engine=work_engine, catalog=work_catalog, sole_api=True)
        try:
            assert previous.executor_id in executors.stale_ids()
        finally:
            work_bootstrap.stop()

    def test_frees_the_passes_its_predecessor_died_running(
        self, work_engine, work_catalog, make_work_executor
    ) -> None:
        # The reconcile lane is global: passes the killed API left running
        # would otherwise keep this process's startup reconcile from running.
        from app.modules.work.submission import nudge

        previous = make_work_executor("previous-api", role="all")
        nudge(JobKind.SOURCES_SCAN)
        (lost,) = [
            execution
            for execution in work_engine.executions.values()
            if isinstance(execution.submission, PassSubmission)
        ]
        lost.status = EngineStatus.RUNNING
        lost.executor_id = previous.executor_id

        work_bootstrap.start(engine=work_engine, catalog=work_catalog, sole_api=True)
        try:
            assert lost.status is EngineStatus.CANCELLED
        finally:
            work_bootstrap.stop()

    def test_frees_its_predecessors_terminal_job_execution(
        self, work_engine, work_catalog, make_work_executor, make_job, db_session
    ) -> None:
        from app.modules.work.submission import execution_id

        previous = make_work_executor("previous-api", role="all")
        job = make_job(
            kind=JobKind.INGESTION_SCRATCH_CLEANUP,
            subject="scratch_window/terminal-proof",
            state=JobState.COMPLETED,
            attempts=1,
            status_json='{"retained_live_writer":true}',
        )
        identifier = execution_id(job.id, job.attempts, job.execution_epoch)
        work_engine.submit(
            JobSubmission(
                execution_id=identifier,
                job_id=job.id,
                definition=job.kind,
                subject_key=job.subject_key,
                lane=LaneName.MAINTENANCE,
                priority=WorkPriority.BACKFILL,
                attempt=job.attempts,
                routing=Deduplicated(f"{job.kind}|{job.subject_key}"),
                execution_epoch=job.execution_epoch,
            )
        )
        lost = work_engine.executions[identifier]
        lost.status = EngineStatus.RUNNING
        lost.executor_id = previous.executor_id

        work_bootstrap.start(engine=work_engine, catalog=work_catalog, sole_api=True)
        try:
            assert lost.status is EngineStatus.CANCELLED
            assert work_engine.lane_depth(LaneName.MAINTENANCE).running == 0
            db_session.expire_all()
            retained = db_session.get(Job, job.id)
            assert retained is not None
            assert retained.state is JobState.COMPLETED
            assert retained.status_json == '{"retained_live_writer":true}'
        finally:
            work_bootstrap.stop()

    def test_a_process_without_the_api_lock_retires_nobody(
        self, work_engine, work_catalog, make_work_executor
    ) -> None:
        # A worker cannot know whether the API it sees is alive.
        make_work_executor("live-api", role="api")

        work_bootstrap.start(engine=work_engine, catalog=work_catalog)
        try:
            assert executors.stale_ids() == set()
        finally:
            work_bootstrap.stop()

    def test_cancels_what_another_application_version_left_running(
        self, work_engine, work_catalog
    ) -> None:
        work_engine.app_version = "0.0.1-previous"
        work_engine.submit(
            JobSubmission(
                execution_id="old-job:1",
                job_id="old-job",
                definition=JobKind.INGESTION_UPLOAD,
                subject_key="ingest_request/old-job",
                lane=LaneName.INGEST,
                priority=WorkPriority.INTERACTIVE,
                attempt=1,
                routing=Deduplicated("ingestion.upload|ingest_request/old-job"),
                execution_epoch="test-epoch",
            )
        )
        work_engine.app_version = settings.app_version

        work_bootstrap.start(engine=work_engine, catalog=work_catalog)
        try:
            evidence = work_engine.evidence(["old-job:1"])["old-job:1"]
        finally:
            work_bootstrap.stop()

        assert evidence.status is EngineStatus.CANCELLED

    def test_publishes_job_changes_to_the_given_publisher(
        self, work_engine, work_catalog
    ) -> None:
        notices: list[tuple[str, dict]] = []

        class Publisher:
            def publish_threadsafe(self, channel, payload):
                notices.append((channel, payload))

        work_bootstrap.start(
            engine=work_engine, catalog=work_catalog, publisher=Publisher()
        )
        try:
            job_id = jobs.create(
                definition=JobKind.WORK_HOUSEKEEPING,
                subject_key="test/1",
                owner_user_id=None,
            )
        finally:
            work_bootstrap.stop()

        assert ("work:admin", {"type": "job", "job_id": job_id}) == (
            notices[0][0],
            {key: notices[0][1][key] for key in ("type", "job_id")},
        )

    def test_refuses_a_topology_before_binding_anything(
        self, work_engine, work_catalog
    ) -> None:
        catalog_module.bind(None, None)
        _overlay["process_role"] = ProcessRole.WORKER
        _overlay["db_url"] = "sqlite:////data/db/printstash.sqlite"

        with pytest.raises(RuntimeError, match="requires PostgreSQL"):
            work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        assert not catalog_module.bound()
        assert work_bootstrap.current() is None

    def test_heartbeats_until_stopped(
        self, work_engine, work_catalog, db_session: Session
    ) -> None:
        _overlay["fence_heartbeat_seconds"] = 1
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)
        try:
            first = db_session.get(WorkExecutor, executors.executor_id())
            assert first is not None
            registered = first.heartbeat_at
            deadline = time.monotonic() + 5
            while True:
                db_session.expire_all()
                row = db_session.get(WorkExecutor, executors.executor_id())
                if row is not None and row.heartbeat_at > registered:
                    break
                assert time.monotonic() < deadline, "no heartbeat"
                time.sleep(0.1)
        finally:
            work_bootstrap.stop()


class TestStop:
    def test_releases_the_fences_this_executor_held(
        self, work_engine, work_catalog
    ) -> None:
        # Another process must not wait out a TTL for a fence nobody holds.
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)
        fences.acquire("backup", holder=executors.executor_id(), reason="test")

        work_bootstrap.stop()

        assert fences.held_by(executors.executor_id()) == []

    def test_forgets_this_executor(
        self, work_engine, work_catalog, db_session: Session
    ) -> None:
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        work_bootstrap.stop()

        db_session.expire_all()
        assert db_session.get(WorkExecutor, executors.executor_id()) is None

    def test_unbinds_the_engine(self, work_engine, work_catalog) -> None:
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        work_bootstrap.stop()

        assert not catalog_module.bound()
        assert work_engine.launched is False

    def test_stopping_twice_is_harmless(self, work_engine, work_catalog) -> None:
        work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        work_bootstrap.stop()
        work_bootstrap.stop()

        assert work_bootstrap.current() is None

    def test_stops_the_heartbeat(self, work_engine, work_catalog) -> None:
        runtime = work_bootstrap.start(engine=work_engine, catalog=work_catalog)

        work_bootstrap.stop()

        assert runtime.heartbeat is not None
        runtime.heartbeat.join(timeout=5)
        assert not runtime.heartbeat.is_alive()


class TestAfterRestore:
    def test_rebuilds_engine_state_from_the_restored_vault(
        self, started, work_engine, db_session: Session
    ) -> None:
        work_engine.drain()
        before = set(work_engine.executions)

        work_bootstrap.after_restore()

        # The restored database is authoritative; what it owes is resubmitted.
        assert not before & set(work_engine.executions)
        assert _passes(work_engine) == set(started.catalog.definitions)
        assert db_session.get(WorkExecutor, executors.executor_id()) is not None

    def test_a_pass_the_snapshot_shows_queued_does_not_suppress_reconciling(
        self, started, work_engine, db_session: Session
    ) -> None:
        # The archive recorded "a pass is already waiting" for the engine of
        # the process that took it. That engine is gone, so the pass never runs.
        work_engine.drain()
        cursor = db_session.get(
            ReconcileCursor, JobKind.SOURCES_SCAN
        ) or ReconcileCursor(source=JobKind.SOURCES_SCAN)
        cursor.pass_queued_at = utcnow()
        cursor.pass_priority = WorkPriority.INTERACTIVE
        db_session.add(cursor)
        db_session.commit()

        work_bootstrap.after_restore()

        assert JobKind.SOURCES_SCAN in _passes(work_engine)

    def test_does_nothing_without_running_work(self) -> None:
        assert work_bootstrap.current() is None

        work_bootstrap.after_restore()

        assert work_bootstrap.current() is None

    def test_starts_the_work_its_interrupted_predecessor_held(
        self, work_engine, work_catalog
    ) -> None:
        # The process started under an interrupted restore's maintenance, so
        # it held its work; the restore that replaces the vault ends that.
        work_bootstrap.hold(engine=work_engine, catalog=work_catalog)
        try:
            work_bootstrap.after_restore()

            assert work_bootstrap.current() is not None
            assert _passes(work_engine) == set(work_catalog.definitions)
        finally:
            work_bootstrap.stop()

    def test_supersedes_restored_backups_without_running_work(
        self, make_job, db_session: Session
    ) -> None:
        # An API that runs no jobs still restores; the database fact holds.
        snapshot = make_job(
            kind=JobKind.BACKUPS_CREATE, state=JobState.RUNNING, attempts=1
        )

        work_bootstrap.after_restore()

        db_session.expire_all()
        row = db_session.get(Job, snapshot.id)
        assert row is not None and row.state == JobState.CANCELLED


class TestHeldWork:
    """Work an interrupted restore held at startup starts once it is resolved.

    A process that starts while a restore's journal still governs holds its
    background work. When recovery resolves that restore, nothing restarts
    the process, so without releasing the held work every nudge would be a
    no-op and nothing background would run until the next restart.
    """

    def test_starts_once_no_restore_governs(self, work_engine, work_catalog) -> None:
        work_bootstrap.hold(engine=work_engine, catalog=work_catalog)
        try:
            assert work_bootstrap.release_held() is True

            assert work_bootstrap.current() is not None
            assert _passes(work_engine) == set(work_catalog.definitions)
        finally:
            work_bootstrap.stop()

    def test_waits_while_a_restore_still_governs(
        self, work_engine, work_catalog
    ) -> None:
        from app.runtime.maintenance import (
            end_restore_maintenance,
            hold_restore_maintenance,
        )

        work_bootstrap.hold(engine=work_engine, catalog=work_catalog)
        hold_restore_maintenance()
        try:
            assert work_bootstrap.release_held() is False

            assert work_bootstrap.current() is None
        finally:
            end_restore_maintenance()
            work_bootstrap.release_held()
            work_bootstrap.stop()

    def test_starts_only_once(self, work_engine, work_catalog) -> None:
        work_bootstrap.hold(engine=work_engine, catalog=work_catalog)
        try:
            work_bootstrap.release_held()
            runtime = work_bootstrap.current()

            assert work_bootstrap.release_held() is False
            assert work_bootstrap.current() is runtime
        finally:
            work_bootstrap.stop()

    def test_work_that_fails_to_start_stays_held(
        self, work_engine, work_catalog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The recovery that called it succeeded; a failed start must neither
        # fail it nor lose the work, which the next resolution starts.
        work_bootstrap.hold(engine=work_engine, catalog=work_catalog)
        real_start = work_bootstrap.start

        def refuse(**_kwargs) -> None:
            raise RuntimeError("engine unavailable")

        monkeypatch.setattr(work_bootstrap, "start", refuse)
        try:
            assert work_bootstrap.release_held() is False
            monkeypatch.setattr(work_bootstrap, "start", real_start)

            assert work_bootstrap.release_held() is True
        finally:
            work_bootstrap.stop()

    def test_nothing_held_starts_nothing(self) -> None:
        assert work_bootstrap.release_held() is False

        assert work_bootstrap.current() is None
