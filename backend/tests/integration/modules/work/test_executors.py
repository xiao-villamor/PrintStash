"""Which processes run work, and whether each is still alive.

A dead executor is recognised by its heartbeat going stale; that is what lets
the reconciler rerun work stranded on it. The heartbeat also carries the
process's in-flight write count, which is how a restore drains work running in
other processes.
"""

from __future__ import annotations

from datetime import timedelta

from sqlmodel import Session

from app.core.config import _overlay, settings
from app.core.time import utcnow
from app.db.models import WorkExecutor
from app.modules.work import executors


class TestExecutorId:
    def test_is_stable_within_a_process(self) -> None:
        assert executors.executor_id() == executors.executor_id()

    def test_names_this_processs_role(self) -> None:
        assert executors.executor_id().startswith(f"{settings.process_role}-")

    def test_an_operator_can_pin_it(self) -> None:
        # A worker container that restarts keeps its id.
        _overlay["executor_id"] = "worker-pinned"
        executors.reset_executor_id()

        assert executors.executor_id() == "worker-pinned"


class TestRegister:
    def test_records_this_process(self, db_session: Session) -> None:
        executors.register(role="worker", lanes=["ingest", "network"])

        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None
        assert (row.role, row.lanes, row.active_mutations) == (
            "worker",
            "ingest,network",
            0,
        )
        assert row.app_version == settings.app_version

    def test_registering_again_refreshes_the_row(self, db_session: Session) -> None:
        executors.register(role="worker", lanes=["ingest"])
        later = utcnow() + timedelta(minutes=1)

        executors.register(role="api", lanes=[], now=later)

        db_session.expire_all()
        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None
        assert (row.role, row.lanes) == ("api", "")


class TestHeartbeat:
    def test_reports_what_the_process_is_doing(self, db_session: Session) -> None:
        executors.register(role="all", lanes=[])
        later = utcnow() + timedelta(seconds=30)

        executors.heartbeat(active_mutations=3, now=later)

        db_session.expire_all()
        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None
        assert row.active_mutations == 3
        assert row.heartbeat_at.replace(tzinfo=None) == later.replace(tzinfo=None)

    def test_never_reports_a_negative_count(self, db_session: Session) -> None:
        executors.register(role="all", lanes=[])

        executors.heartbeat(active_mutations=-2)

        db_session.expire_all()
        row = db_session.get(WorkExecutor, executors.executor_id())
        assert row is not None and row.active_mutations == 0

    def test_a_forgotten_executor_is_not_recreated(self, db_session: Session) -> None:
        executors.heartbeat(active_mutations=1)

        assert db_session.get(WorkExecutor, executors.executor_id()) is None


class TestLiveness:
    def test_staleness_is_read_in_a_session_the_caller_has_open(
        self, make_work_executor, db_session: Session, transactions: list[str]
    ) -> None:
        dead = make_work_executor("dead", stale=True)
        make_work_executor("alive")
        db_session.commit()
        transactions.clear()

        stale = executors.stale_ids(session=db_session)

        assert (stale, len(transactions)) == ({dead.executor_id}, 1)

    def test_a_silent_executor_is_stale(self, make_work_executor) -> None:
        dead = make_work_executor("dead", stale=True)
        alive = make_work_executor("alive")

        assert executors.stale_ids() == {dead.executor_id}
        assert [row.executor_id for row in executors.live()] == [alive.executor_id]

    def test_forgets_executors_silent_for_ten_stale_windows(
        self, make_work_executor, db_session: Session
    ) -> None:
        long_gone = make_work_executor("long-gone", stale=True)
        long_gone.heartbeat_at = utcnow() - timedelta(
            seconds=settings.jobs_executor_stale_seconds * 11
        )
        db_session.add(long_gone)
        db_session.commit()
        make_work_executor("recently-dead", stale=True)

        assert executors.forget_stale() == 1

        db_session.expire_all()
        assert db_session.get(WorkExecutor, "long-gone") is None
        assert db_session.get(WorkExecutor, "recently-dead") is not None

    def test_deregistering_forgets_this_executor(self, db_session: Session) -> None:
        executors.register(role="all", lanes=[])

        executors.deregister()

        db_session.expire_all()
        assert db_session.get(WorkExecutor, executors.executor_id()) is None


class TestRetirePredecessors:
    def test_a_previous_api_process_is_stale_at_once(self, make_work_executor) -> None:
        previous = make_work_executor("previous-api", role="all")

        assert executors.retire_predecessors() == 1

        assert executors.stale_ids() == {previous.executor_id}

    def test_a_worker_is_never_retired(self, make_work_executor) -> None:
        # Workers share no lock with the API, so one may well be alive.
        make_work_executor("busy-worker", role="worker")

        assert executors.retire_predecessors() == 0
        assert executors.stale_ids() == set()

    def test_this_process_is_never_retired(self, db_session: Session) -> None:
        executors.register(role="all", lanes=[])

        assert executors.retire_predecessors() == 0
        assert executors.executor_id() not in executors.stale_ids()

    def test_an_already_stale_predecessor_keeps_its_heartbeat(
        self, make_work_executor, db_session: Session
    ) -> None:
        gone = make_work_executor("gone-api", role="api", stale=True)
        before = gone.heartbeat_at

        assert executors.retire_predecessors() == 0

        db_session.expire_all()
        assert db_session.get(WorkExecutor, "gone-api").heartbeat_at == before
