"""Derived work waits while the library is still being added to.

A large import shows all its Models first; their thumbnails and measurements
start once the last addition settles, instead of competing with it file by
file. See ``JobDefinition.adds_to_library`` and ``waits_for_additions``.
"""

from __future__ import annotations

import time

import pytest
from sqlmodel import Session, select

from app.db.models import Job, JobKind, JobState, LaneName, ReconcileCursor
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import Lane, WorkCatalog, default_lanes
from app.modules.work.contracts import (
    JobDefinition,
    JobOutcome,
    PassSubmission,
    Step,
    WorkItem,
)
from app.modules.work.reconciler import PassNote, run_pass
from app.modules.work.runner import ExecutionContext
from app.runtime.engine.inline import InlineJobEngine

ADDING = JobKind.INGESTION_UPLOAD
ALSO_ADDING = JobKind.SOURCES_SCAN
WAITING = JobKind.DERIVATIVES_MESH
EAGER = JobKind.DERIVATIVES_VIEWER_STL
LANE = LaneName.MAINTENANCE


class _Pending:
    def pending(self, session, *, now, limit):
        return [WorkItem("artifact/1")][:limit]

    def next_due(self, session, *, now):
        return None


def _noop(_ctx) -> None:
    return None


@pytest.fixture
def engine() -> InlineJobEngine:
    def definition(name: JobKind, **kw) -> JobDefinition:
        return JobDefinition(
            name=name,
            lane=LANE,
            steps=(Step(f"{name}.run", _noop),),
            label=name.value,
            **kw,
        )

    catalog = WorkCatalog(
        [
            definition(ADDING, adds_to_library=True),
            definition(ALSO_ADDING, adds_to_library=True),
            definition(WAITING, source=_Pending(), waits_for_additions=True),
            definition(EAGER, source=_Pending()),
        ],
        lanes={**default_lanes(), LANE: Lane(LANE, 4)},
    )
    built = InlineJobEngine(catalog)
    built.launch(listen_lanes=None)
    catalog_module.bind(built, catalog)
    return built


def _created(session: Session, kind: JobKind) -> list[Job]:
    return list(session.exec(select(Job).where(Job.kind == kind)).all())


def _context(job: Job) -> ExecutionContext:
    return ExecutionContext(
        job_id=job.id,
        definition=job.kind,
        subject_key=job.subject_key,
        priority=job.priority,
        execution_id=f"{job.id}:{job.execution_epoch}:1",
        attempt=1,
        execution_epoch=job.execution_epoch,
        started=time.monotonic(),
    )


def _nudged(engine: InlineJobEngine) -> set[JobKind]:
    return {
        ex.submission.source
        for ex in engine.executions.values()
        if isinstance(ex.submission, PassSubmission)
    }


class TestCatalog:
    def test_lists_the_definitions_that_add_to_the_library(
        self, engine: InlineJobEngine
    ) -> None:
        assert set(catalog_module.get_catalog().additions) == {ADDING, ALSO_ADDING}

    def test_lists_the_definitions_that_wait_for_them(
        self, engine: InlineJobEngine
    ) -> None:
        assert catalog_module.get_catalog().waiting_for_additions == (WAITING,)


class TestDiscovery:
    def test_holds_back_while_an_addition_is_active(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        make_job(kind=ADDING, state=JobState.RUNNING, attempts=1)

        result = run_pass(WAITING)

        assert (_created(db_session, WAITING), result.outcomes) == (
            [],
            {PassNote.WAITING_FOR_ADDITIONS.value: 1},
        )

    def test_finds_work_once_no_addition_is_active(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        make_job(kind=ADDING, state=JobState.COMPLETED)

        run_pass(WAITING)

        assert len(_created(db_session, WAITING)) == 1

    def test_work_that_does_not_wait_is_found_meanwhile(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        make_job(kind=ADDING, state=JobState.RUNNING, attempts=1)

        run_pass(EAGER)

        assert len(_created(db_session, EAGER)) == 1


class TestRelease:
    def test_the_last_addition_to_end_nudges_the_waiting_work(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=ADDING, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.COMPLETED)

        cursor = db_session.get(ReconcileCursor, WAITING)
        assert cursor is not None and cursor.nudged_at is not None
        assert WAITING in _nudged(engine)

    def test_an_addition_that_ends_before_others_leaves_it_waiting(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        make_job(kind=ALSO_ADDING, state=JobState.RUNNING, attempts=1)
        job = make_job(kind=ADDING, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.COMPLETED)

        assert WAITING not in _nudged(engine)

    def test_a_failed_addition_releases_the_waiting_work_too(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=ADDING, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.FAILED, error="broken_upload")

        assert WAITING in _nudged(engine)


class TestDeclarations:
    """The real catalog: what imports and derived outputs declare."""

    @pytest.mark.parametrize(
        "kind",
        [
            JobKind.INGESTION_UPLOAD,
            JobKind.INGESTION_ARCHIVE_SELECTION,
            JobKind.INGESTION_URL,
            JobKind.INGESTION_URL_SELECTION,
            JobKind.INGESTION_COLLECTION,
            JobKind.INGESTION_INBOX_IMPORT,
            JobKind.INGESTION_LIBRARY_IMPORT,
            JobKind.INGESTION_ARTIFACT_UPLOAD,
            JobKind.SOURCES_SCAN,
        ],
    )
    def test_imports_add_to_the_library(self, kind: JobKind) -> None:
        from app.bootstrap.work import definitions

        assert {item.name: item for item in definitions()}[kind].adds_to_library

    def test_inspecting_an_archive_adds_nothing(self) -> None:
        from app.bootstrap.work import definitions

        by_name = {item.name: item for item in definitions()}
        assert not by_name[JobKind.INGESTION_ARCHIVE_INSPECT].adds_to_library

    @pytest.mark.parametrize(
        "kind,waits",
        [
            (JobKind.DERIVATIVES_MESH, True),
            (JobKind.DERIVATIVES_GCODE, True),
            (JobKind.DERIVATIVES_TOOLPATH, True),
            (JobKind.DERIVATIVES_VIEWER_STL, False),
        ],
    )
    def test_derived_outputs_wait_except_the_viewer_mesh(
        self, kind: JobKind, waits: bool
    ) -> None:
        from app.bootstrap.work import definitions

        assert {item.name: item for item in definitions()}[
            kind
        ].waits_for_additions is waits
