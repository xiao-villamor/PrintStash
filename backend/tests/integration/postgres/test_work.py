"""The work layer's database contracts on a real PostgreSQL server.

Background work leans on the database for every guarantee the engine does not
give: one active Job per subject (a partial unique index), one holder per
fence and per reconcile pass (a primary-key race), history that retention can
prune without breaking the rows that point at it (foreign-key actions the
migrations render), and the derivative anti-join. SQLite runs all of it in the
ordinary suite, serialised by its single writer; PostgreSQL runs it with real
concurrent transactions and its own rendering of every index and constraint,
which is what a split topology deploys.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from threading import Barrier

import pytest
from sqlmodel import Session, col, create_engine, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.migrate import run_migrations
from app.db.models import (
    ArtifactUploadSession,
    DerivativeKind,
    InboxItem,
    IngestRequest,
    Job,
    JobKind,
    JobState,
    LaneName,
    WorkPriority,
)
from app.db.session import (
    SQLiteSessionFactory,
    get_session_factory,
    override_session_factory,
)
from app.db.url import normalize_database_url
from app.modules.derivatives.kinds import group
from app.modules.derivatives.source import DerivativeSource, subject_key
from app.modules.work import fences
from app.modules.work.contracts import (
    DiscoveryBudget,
    EngineStatus,
    JobDefinition,
    JobSubmission,
    Lane,
    LaneOrder,
    Step,
    WorkItem,
)
from app.modules.work.jobs import ActiveJobExists, JobStore
from app.modules.work.reconciler import (
    PassResult,
    _discover,
    _discovery_budget,
    run_pass,
)
from tests import factories as f
from tests.containers import fresh_postgres_database


@pytest.fixture(scope="module")
def pg_url() -> str:
    url = fresh_postgres_database("work")
    run_migrations(url)
    return normalize_database_url(url)


@pytest.fixture
def pg(pg_url: str) -> Iterator[Session]:
    """A migrated PostgreSQL vault every session factory in the test points at."""
    engine = create_engine(pg_url, pool_size=8)
    with engine.begin() as connection:
        tables = connection.exec_driver_sql(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version'"
        ).scalars()
        quoted = ", ".join(f'"{table}"' for table in tables)
        connection.exec_driver_sql(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
    previous = get_session_factory()
    override_session_factory(SQLiteSessionFactory(engine))
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        override_session_factory(previous)
        engine.dispose()


def _race(count: int, attempt) -> list:
    """Run ``attempt`` in ``count`` threads released at the same instant."""
    barrier = Barrier(count)
    factory = get_session_factory()

    def run(index: int):
        override_session_factory(factory)
        barrier.wait()
        try:
            return attempt(index)
        except Exception as error:  # noqa: BLE001 - the loser's error is the result
            return error

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


def _expired() -> dict:
    return {
        "state": JobState.COMPLETED,
        "updated_at": utcnow() - timedelta(days=settings.jobs_retention_days, hours=1),
    }


class TestOneActiveJobPerSubject:
    def test_concurrent_claims_on_one_subject_admit_one_job(self, pg) -> None:
        store = JobStore()

        outcomes = _race(
            4,
            lambda _index: store.create(
                definition=JobKind.SOURCES_SCAN,
                subject_key="library/1",
                owner_user_id=None,
            ),
        )

        winners = [outcome for outcome in outcomes if isinstance(outcome, str)]
        assert len(winners) == 1
        assert all(
            isinstance(outcome, ActiveJobExists)
            for outcome in outcomes
            if outcome not in winners
        )

    def test_a_finished_job_does_not_hold_its_subject(self, pg) -> None:
        f.build_job(pg, kind=JobKind.SOURCES_SCAN, subject="library/1", **_expired())

        assert JobStore().create(
            definition=JobKind.SOURCES_SCAN, subject_key="library/1", owner_user_id=None
        )


class TestRetention:
    def test_a_pending_import_outlives_the_job_that_resolved_it(self, pg) -> None:
        owner = f.build_user(pg, "pg-inbox-owner")
        job = f.build_job(pg, owner=owner, **_expired())
        item = f.build_inbox_item(pg, owner, job_id=job.id)

        assert JobStore().prune() == 1

        pg.expire_all()
        kept = pg.get(InboxItem, item.id)
        assert kept is not None and kept.job_id is None

    def test_an_upload_outlives_the_job_that_finalized_it(self, pg) -> None:
        owner = f.build_user(pg, "pg-upload-owner")
        job = f.build_job(pg, owner=owner, **_expired())
        upload = f.build_artifact_upload(pg, owner, job_id=job.id)

        assert JobStore().prune() == 1

        pg.expire_all()
        kept = pg.get(ArtifactUploadSession, upload.id)
        assert kept is not None and kept.job_id is None

    def test_a_pruned_ingest_job_takes_its_request_with_it(self, pg) -> None:
        request = f.build_ingest_request(
            pg, f.build_user(pg, "pg-ingest-owner"), state=JobState.COMPLETED
        )
        job = pg.get(Job, request.job_id)
        assert job is not None
        job.finished_at = _expired()["updated_at"]
        pg.add(job)
        pg.commit()
        job_id = job.id

        assert JobStore().prune() == 1

        pg.expire_all()
        assert pg.get(IngestRequest, job_id) is None


class TestFences:
    def test_concurrent_acquisitions_admit_one_holder(self, pg) -> None:
        outcomes = _race(
            4,
            lambda index: fences.acquire(
                fences.RESTORE, holder=f"process-{index}", reason="restore"
            ),
        )

        held = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
        assert len(held) == 1
        assert all(
            isinstance(outcome, fences.FenceHeld)
            for outcome in outcomes
            if outcome not in held
        )


class TestReconcile:
    def test_concurrent_passes_create_one_job_per_subject(self, pg) -> None:
        # Two processes' ticks land on one source at once: the pass claim and
        # the active-subject index together keep every subject single-flight.
        artifacts = [
            f.build_file(pg, f.build_model(pg, f"Part {n}"), filename=f"part{n}.stl")
            for n in range(3)
        ]

        _race(
            2,
            lambda index: run_pass(JobKind.DERIVATIVES_MESH, holder=f"process-{index}"),
        )

        # A pass is bounded by its lane's headroom, so it may stop short of
        # every subject (a completion nudge continues it); it never doubles one.
        pg.expire_all()
        subjects = pg.exec(
            select(Job.subject_key).where(Job.kind == JobKind.DERIVATIVES_MESH)
        ).all()
        assert subjects
        assert len(subjects) == len(set(subjects))
        assert set(subjects) <= {subject_key(artifact.id) for artifact in artifacts}


class TestDerivativeSource:
    def test_the_anti_join_finds_only_what_is_owed(self, pg) -> None:
        model = f.build_model(pg, "Anti-join")
        owed = f.build_file(pg, model, filename="owed.stl")
        done = f.build_file(pg, model, filename="done.stl")
        f.build_derivative(pg, done, DerivativeKind.METADATA)
        f.build_derivative(pg, done, DerivativeKind.THUMBNAIL)

        pending = DerivativeSource(group(JobKind.DERIVATIVES_MESH)).pending(
            pg, now=utcnow(), limit=10
        )

        assert [item.subject_key for item in pending] == [subject_key(owed.id)]


class TestStagingDiscard:
    def test_discard_serializes_against_retry(self, pg, tmp_path, monkeypatch):
        import hashlib

        from app.core.errors import OperationError
        from app.db.models import IngestRequestKind, StagingLease
        from app.modules.ingestion import staging_cleanup, staging_leases
        from app.modules.work import service

        owner = f.build_user(pg, "pg-staging-owner")
        request = f.build_ingest_request(
            pg, owner, kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
        )
        path = tmp_path / "retry.stl"
        path.write_bytes(b"original retry input")
        lease = staging_leases.create_job_lease(
            pg,
            job_id=request.job_id,
            owner_user_id=owner.id,
            path=path,
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        pg.commit()
        job_id, lease_id = request.job_id, lease.id
        _ = owner.id, owner.is_superuser
        pg.expunge(owner)
        monkeypatch.setattr(service, "nudge", lambda *_args: None)

        def attempt(index):
            action = staging_cleanup.discard if index == 0 else service.retry
            try:
                action(job_id, actor=owner)
                return "accepted"
            except OperationError as error:
                return error.code

        results = _race(2, attempt)
        pg.expire_all()
        job = pg.get(Job, job_id)
        assert job is not None
        retained = pg.get(StagingLease, lease_id)
        if job.state == JobState.QUEUED:
            assert results == ["staging_job_not_terminal", "accepted"]
            assert retained is not None
            assert path.read_bytes() == b"original retry input"
        else:
            assert results == ["accepted", "job_subject_gone"]
            assert retained is None
            assert not path.exists()


@dataclass
class _PriorityAdmissionSource:
    items: tuple[WorkItem, ...]
    barrier: Barrier | None = None

    def pending(self, session, *, now, limit):
        return self.items[:limit]

    def pending_prioritized(self, session, *, now, budget: DiscoveryBudget):
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        return self.items[: budget.total]

    def next_due(self, session, *, now):
        return None


def _priority_admission_step(_context) -> None:
    pass


@pytest.fixture
def priority_catalog(work_catalog, work_engine, monkeypatch):
    """Two definitions share the real engine's one-slot maintenance lane."""
    monkeypatch.setattr(work_catalog, "definitions", dict(work_catalog.definitions))
    monkeypatch.setitem(
        work_catalog.lanes,
        LaneName.MAINTENANCE,
        Lane(LaneName.MAINTENANCE, 1, queue_order=LaneOrder.FIFO),
    )
    definitions = tuple(
        JobDefinition(
            name=kind,
            lane=LaneName.MAINTENANCE,
            steps=(Step(f"{kind.value}.priority_probe", _priority_admission_step),),
            label="Postgres priority qualification",
            source=_PriorityAdmissionSource(
                (
                    WorkItem(
                        f"pg-priority/{kind.value}", priority=WorkPriority.INTERACTIVE
                    ),
                )
            ),
        )
        for kind in (JobKind.INGESTION_UPLOAD, JobKind.SOURCES_SCAN)
    )
    for definition in definitions:
        monkeypatch.setitem(work_catalog.definitions, definition.name, definition)
    return definitions


class TestPriorityAdmission:
    def test_concurrent_sources_share_one_interactive_queue_slot(
        self, pg, priority_catalog, work_engine
    ):
        barrier = Barrier(2)
        first, second = priority_catalog
        assert isinstance(first.source, _PriorityAdmissionSource)
        assert isinstance(second.source, _PriorityAdmissionSource)
        first.source.barrier = barrier
        second.source.barrier = barrier

        def discover(index):
            _discover(priority_catalog[index], now=utcnow(), result=PassResult())

        outcomes = _race(2, discover)
        assert outcomes == [None, None]
        pg.expire_all()
        queued = pg.exec(
            select(Job).where(
                col(Job.kind).in_(tuple(item.name for item in priority_catalog)),
                Job.state == JobState.QUEUED,
                Job.priority == WorkPriority.INTERACTIVE,
            )
        ).all()
        assert len(queued) == 1
        assert queued[0].kind in tuple(item.name for item in priority_catalog)
        assert queued[0].attempts == 1
        executions = list(work_engine.executions.values())
        assert len(executions) == 1
        execution = executions[0]
        assert isinstance(execution.submission, JobSubmission)
        assert execution.submission.job_id == queued[0].id
        assert execution.submission.attempt == 1
        assert execution.status is EngineStatus.QUEUED

    def test_running_backfill_preserves_interactive_queue_capacity(
        self, pg, priority_catalog
    ):
        background, interactive = priority_catalog
        running = f.build_job(
            pg,
            kind=background.name,
            state=JobState.RUNNING,
            subject="pg-priority/running-backfill",
            priority=WorkPriority.BACKFILL,
            attempts=1,
        )
        initial = _discovery_budget(pg, interactive)
        assert initial == DiscoveryBudget(total=1, interactive=1, backfill=0)

        _discover(interactive, now=utcnow(), result=PassResult())

        pg.expire_all()
        queued = pg.exec(
            select(Job).where(
                Job.kind == interactive.name,
                Job.state == JobState.QUEUED,
                Job.priority == WorkPriority.INTERACTIVE,
            )
        ).all()
        assert len(queued) == 1
        assert queued[0].subject_key == f"pg-priority/{interactive.name.value}"
        assert _discovery_budget(pg, interactive) == DiscoveryBudget(
            total=0, interactive=0, backfill=0
        )
        retained = pg.get(Job, running.id)
        assert retained is not None
        assert retained.state is JobState.RUNNING
        assert retained.priority is WorkPriority.BACKFILL
        assert retained.attempts == 1

    def test_concurrent_sources_share_one_backfill_execution_slot(
        self, pg, priority_catalog, work_engine
    ):
        barrier = Barrier(2)
        first, second = priority_catalog
        assert isinstance(first.source, _PriorityAdmissionSource)
        assert isinstance(second.source, _PriorityAdmissionSource)
        first.source.items = (
            WorkItem("pg-backfill/first", priority=WorkPriority.BACKFILL),
        )
        second.source.items = (
            WorkItem("pg-backfill/second", priority=WorkPriority.BACKFILL),
        )
        first.source.barrier = barrier
        second.source.barrier = barrier

        def discover(index):
            _discover(priority_catalog[index], now=utcnow(), result=PassResult())

        assert _race(2, discover) == [None, None]
        pg.expire_all()
        queued = pg.exec(
            select(Job).where(
                Job.state == JobState.QUEUED,
                Job.priority == WorkPriority.BACKFILL,
            )
        ).all()
        assert len(queued) == 1
        job = queued[0]
        assert job.kind in (first.name, second.name)
        assert job.backfill_admission_epoch == job.execution_epoch
        assert job.submitted_epoch == job.execution_epoch
        assert job.attempts == 1
        assert _discovery_budget(pg, first).backfill == 0
        assert _discovery_budget(pg, second).backfill == 0
        executions = list(work_engine.executions.values())
        assert len(executions) == 1
        execution = executions[0]
        assert isinstance(execution.submission, JobSubmission)
        assert execution.submission.job_id == job.id
        assert execution.submission.execution_epoch == job.execution_epoch
        assert execution.submission.attempt == 1
        assert execution.submission.priority is WorkPriority.BACKFILL
        assert execution.status is EngineStatus.QUEUED
