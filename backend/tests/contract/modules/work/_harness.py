"""One harness, two engines: inline and DBOS on SQLite or PostgreSQL.

Every other tier runs background work on ``InlineJobEngine``. That is only
sound if it keeps the same promises as the engine production runs, so this
tier holds both to one contract with the same test bodies.

The harness hides the one real difference, time: the inline engine runs
nothing until it is drained and fakes time with a virtual clock, while DBOS
runs executions on its own threads in real time. ``settle`` drains or waits;
``elapse`` advances the clock or sleeps.

DBOS executes on threads it creates, which see the process's default session
factory, not a test override. So the harness builds the application schema on
that default database and points the test's own sessions at it too: both
sides of every assertion read the same rows.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlmodel import SQLModel, create_engine

from app.db import session as db_session_module
from app.db.models import Job, JobKind, JobState, LaneName, WorkPriority
from app.db.session import get_session_factory, override_session_factory
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import WorkCatalog, default_lanes
from app.modules.work.contracts import (
    JobContext,
    JobDefinition,
    JobEngine,
    Lane,
    RetryPolicy,
    Step,
    WorkItem,
)
from app.modules.work.jobs import TERMINAL_STATES, jobs

# The contract catalog borrows real lanes and kinds; it holds only these four
# definitions, so nothing else answers to those names.
FAST = LaneName.DERIVE_LIGHT  # a per-worker lane, concurrency four
SERIAL = LaneName.MAINTENANCE  # a global lane, concurrency one

PLAIN = JobKind.INGESTION_UPLOAD
RETRYING = JobKind.INGESTION_URL
SERIAL_JOB = JobKind.BACKUPS_CREATE
DISCOVERED = JobKind.SOURCES_SCAN


class Flaky(Exception):
    """A transient failure the retrying step's policy retries."""


@dataclass
class Recorder:
    """What executions did, shared with the engine's threads."""

    steps: list[tuple[str, str]] = field(default_factory=list)
    behaviour: dict[str, Callable[[JobContext], None]] = field(default_factory=dict)
    discover: set[str] = field(default_factory=set)
    running: int = 0
    peak: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def enter(self, subject: str, step: str) -> None:
        with self.lock:
            self.steps.append((subject, step))
            self.running += 1
            self.peak = max(self.peak, self.running)

    def leave(self) -> None:
        with self.lock:
            self.running -= 1

    def firsts(self) -> list[str]:
        with self.lock:
            return [subject for subject, step in self.steps if step == "first"]


RECORD = Recorder()


def _first(ctx: JobContext) -> None:
    RECORD.enter(ctx.subject_key, "first")
    try:
        behaviour = RECORD.behaviour.get(ctx.subject_key)
        if behaviour is not None:
            behaviour(ctx)
    finally:
        RECORD.leave()


def _found(ctx: JobContext) -> None:
    """Doing the discovered work is what removes it from the source."""
    _first(ctx)
    RECORD.discover.discard(ctx.subject_key)


def _second(ctx: JobContext) -> None:
    RECORD.enter(ctx.subject_key, "second")
    RECORD.leave()


class _DiscoverySource:
    def pending(self, session, *, now, limit):
        del session, now
        return [
            WorkItem(subject_key=subject, priority=WorkPriority.INTERACTIVE)
            for subject in sorted(RECORD.discover)[:limit]
        ]

    def next_due(self, session, *, now):
        del session, now
        return None


def _drop_discovered(_session, subject: str) -> None:
    RECORD.discover.discard(subject)


def contract_catalog() -> WorkCatalog:
    lanes = {
        **default_lanes(),
        FAST: Lane(FAST, 4),
        SERIAL: Lane(SERIAL, 1, scope="global"),
    }
    first = Step(f"{PLAIN}.first", _first)
    return WorkCatalog(
        [
            JobDefinition(
                name=PLAIN,
                lane=FAST,
                steps=(first, Step(f"{PLAIN}.second", _second)),
                label="Plain",
            ),
            JobDefinition(
                name=RETRYING,
                lane=FAST,
                steps=(
                    Step(
                        f"{RETRYING}.first",
                        _first,
                        RetryPolicy(
                            max_attempts=3, interval_seconds=0.01, retry_on=(Flaky,)
                        ),
                    ),
                ),
                label="Retrying",
            ),
            JobDefinition(
                name=SERIAL_JOB,
                lane=SERIAL,
                steps=(Step(f"{SERIAL_JOB}.first", _first),),
                label="Serial",
            ),
            JobDefinition(
                name=DISCOVERED,
                lane=FAST,
                steps=(Step(f"{DISCOVERED}.first", _found),),
                source=_DiscoverySource(),
                cancel=_drop_discovered,
                label="Discovered",
            ),
        ],
        lanes=lanes,
    )


class Harness:
    kind: str
    engine: JobEngine

    def __init__(self, catalog: WorkCatalog) -> None:
        self.catalog = catalog

    # -- time ----------------------------------------------------------------

    def settle(self, timeout: float = 30.0) -> None:
        raise NotImplementedError

    def elapse(self, seconds: float) -> None:
        raise NotImplementedError

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 30.0) -> None:
        raise NotImplementedError

    def relaunch(
        self, *, app_version: str, listen_lanes: list[LaneName] | None
    ) -> None:
        raise NotImplementedError

    def started(self, subject: str) -> None:
        """Wait until ``subject``'s first step is running (a no-op inline).

        Inline, nothing runs until the test settles, so a gated execution is
        simply queued; the test opens its gate before settling.
        """

    # -- work ----------------------------------------------------------------

    def job(
        self,
        definition: JobKind,
        subject: str,
        *,
        priority: WorkPriority = WorkPriority.INTERACTIVE,
        behaviour: Callable[[JobContext], None] | None = None,
    ) -> str:
        if behaviour is not None:
            RECORD.behaviour[subject] = behaviour
        return jobs.create(
            definition=definition,
            subject_key=subject,
            owner_user_id=None,
            priority=priority,
        )

    def state(self, job_id: str) -> JobState:
        status = jobs.get(job_id)
        assert status is not None
        return status.state

    def all_settled(self) -> bool:
        from sqlmodel import col, select

        with get_session_factory().scoped_session() as session:
            return not session.exec(
                select(Job.id).where(col(Job.state).not_in(list(TERMINAL_STATES)))
            ).first()


class InlineHarness(Harness):
    kind = "inline"

    def __init__(self, catalog: WorkCatalog) -> None:
        from app.runtime.engine.inline import InlineJobEngine

        super().__init__(catalog)
        self.engine = InlineJobEngine(catalog)
        self.engine.launch(listen_lanes=None)

    def settle(self, timeout: float = 30.0) -> None:
        del timeout
        self.engine.drain()  # type: ignore[attr-defined]

    def elapse(self, seconds: float) -> None:
        self.engine.advance(seconds)  # type: ignore[attr-defined]

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 30.0) -> None:
        del timeout
        # Nothing runs concurrently: whatever the test waits for either holds
        # once queued work runs, or never will.
        self.engine.drain()  # type: ignore[attr-defined]
        assert predicate()

    def relaunch(
        self, *, app_version: str, listen_lanes: list[LaneName] | None
    ) -> None:
        self.engine.app_version = app_version  # type: ignore[attr-defined]
        self.engine.launch(listen_lanes=listen_lanes)

    def close(self) -> None:
        self.engine.shutdown()


class DbosHarness(Harness):
    kind = "dbos"

    def __init__(
        self,
        catalog: WorkCatalog,
        system_db: str,
        *,
        schema: str | None = None,
        app_version: str | None = None,
        tick_seconds: int = 3600,
        listen_lanes: list[LaneName] | None = None,
    ) -> None:
        super().__init__(catalog)
        self.system_db = system_db
        self.schema = schema
        self.tick_seconds = tick_seconds
        self.engine = self._build(app_version=app_version)
        self.engine.launch(listen_lanes=listen_lanes)

    def _build(self, *, app_version: str | None) -> JobEngine:
        from app.runtime.engine.dbos_engine import DbosJobEngine

        return DbosJobEngine(
            self.catalog,
            system_database_url=self.system_db,
            schema=self.schema,
            executor_id="contract-executor",
            app_version=app_version,
            polling_interval_seconds=0.1,
            tick_seconds=self.tick_seconds,
        )

    def settle(self, timeout: float = 30.0) -> None:
        self.wait_for(self.all_settled, timeout)
        # Completion nudges run after the Job settles; let them drain too.
        time.sleep(0.3)
        self.wait_for(self.all_settled, timeout)

    def elapse(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, "engine did not converge"
            time.sleep(0.05)

    def relaunch(
        self, *, app_version: str, listen_lanes: list[LaneName] | None
    ) -> None:
        self.engine.shutdown()
        self.engine = self._build(app_version=app_version)
        self.engine.launch(listen_lanes=listen_lanes)
        catalog_module.bind(self.engine, self.catalog)

    def started(self, subject: str) -> None:
        self.wait_for(lambda: subject in RECORD.firsts())

    def close(self) -> None:
        self.engine.shutdown()


@contextmanager
def shared_app_db(path: Path | None = None) -> Iterator[None]:
    """One application database shared by callers and DBOS-created threads.

    A supplied path creates an isolated current schema. Replacing the ContextVar
    itself gives threads created by DBOS the same default factory as the test;
    restoring the original object preserves the caller's previous override.
    """
    if path is None:
        default = db_session_module._default_factory
        engine = db_session_module.get_engine()
        SQLModel.metadata.create_all(engine)
        _empty(engine)
        override_session_factory(default)
        try:
            yield
        finally:
            _empty(engine)
        return

    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    event.listen(engine, "connect", db_session_module._set_sqlite_pragmas)
    factory = db_session_module.SQLiteSessionFactory(engine)
    previous_factory = db_session_module._default_factory
    previous_context = db_session_module._factory_ctx
    db_session_module._default_factory = factory
    db_session_module._factory_ctx = ContextVar("session_factory", default=factory)
    override_session_factory(factory)
    try:
        SQLModel.metadata.create_all(engine)
        yield
    finally:
        db_session_module._factory_ctx = previous_context
        db_session_module._default_factory = previous_factory
        factory.dispose()


def _empty(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        # Foreign keys are off, so the order does not matter (and the models'
        # files/models cycle has no valid order anyway).
        for table in SQLModel.metadata.tables.values():
            connection.execute(table.delete())
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


@contextmanager
def harness_for(kind: str, tmp_path: Path) -> Iterator[Harness]:
    RECORD.steps.clear()
    RECORD.behaviour.clear()
    RECORD.discover.clear()
    RECORD.running = RECORD.peak = 0
    catalog = contract_catalog()
    built: Harness = (
        InlineHarness(catalog)
        if kind == "inline"
        else DbosHarness(catalog, f"sqlite:///{tmp_path / 'engine.sqlite'}")
    )
    catalog_module.bind(built.engine, catalog)
    try:
        yield built
    finally:
        # Release anything a test left blocked before the engine stops.
        for behaviour in list(RECORD.behaviour.values()):
            gate = getattr(behaviour, "gate", None)
            if gate is not None:
                gate.set()
        built.close()  # type: ignore[attr-defined]
        catalog_module.bind(None, None)


def gated(gate: threading.Event) -> Callable[[Any], None]:
    """A step body that holds its execution until ``gate`` opens."""

    def hold(_ctx: Any) -> None:
        assert gate.wait(30), "gate never opened"

    hold.gate = gate  # type: ignore[attr-defined]
    return hold
