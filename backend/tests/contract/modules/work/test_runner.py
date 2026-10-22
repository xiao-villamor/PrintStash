"""A running interactive attempt makes room for bounded discovery.

The real engine holds the first execution open. Its successor must reach the
queue before that execution settles, without borrowing the backfill allowance
or bypassing the FIFO lane's concurrency.
"""

from __future__ import annotations

import threading
from dataclasses import replace

from sqlmodel import select

from app.db.models import Job, JobState, WorkPriority
from app.db.session import get_session_factory
from app.modules.work import submission
from app.modules.work.contracts import DiscoveryBudget, LaneOrder, WorkItem
from app.modules.work.jobs import active_job_predicate
from app.modules.work.reconciler import run_pass
from tests.contract.modules.work._harness import (
    DISCOVERED,
    RECORD,
    SERIAL,
    contract_catalog,
    gated,
    harness_for,
    shared_app_db,
)


class _PrioritySource:
    def pending(self, session, *, now, limit):
        return self.pending_prioritized(
            session,
            now=now,
            budget=DiscoveryBudget(total=limit, interactive=limit, backfill=0),
        )

    def next_due(self, session, *, now):
        return None

    def pending_prioritized(self, session, *, now, budget):
        active = set(
            session.exec(
                select(Job.subject_key).where(
                    Job.kind == DISCOVERED, active_job_predicate()
                )
            ).all()
        )
        pending = sorted(RECORD.discover - active)
        chosen = []
        for priority, room in (
            (WorkPriority.INTERACTIVE, budget.interactive),
            (WorkPriority.BACKFILL, budget.backfill),
        ):
            chosen.extend(
                WorkItem(subject_key=subject, priority=priority)
                for subject in [
                    item for item in pending if item.startswith(f"{priority.value}/")
                ][:room]
            )
        return chosen[: budget.total]


def _states() -> dict[str, JobState]:
    with get_session_factory().scoped_session() as session:
        return dict(
            session.exec(
                select(Job.subject_key, Job.state).where(Job.kind == DISCOVERED)
            ).all()
        )


class TestBegin:
    def test_running_interactive_job_prepares_bounded_successors(
        self, tmp_path, dbos_databases
    ) -> None:
        catalog = contract_catalog()
        catalog.lanes[SERIAL] = replace(
            catalog.lanes[SERIAL], queue_order=LaneOrder.FIFO
        )
        catalog.definitions[DISCOVERED] = replace(
            catalog.definition(DISCOVERED), lane=SERIAL, source=_PrioritySource()
        )
        app_url, system_db = dbos_databases
        with (
            shared_app_db(app_url),
            harness_for(
                "dbos", tmp_path, catalog=catalog, system_db=system_db
            ) as harness,
        ):
            gate = threading.Event()
            RECORD.discover.update(
                {
                    "interactive/0",
                    "interactive/1",
                    "interactive/2",
                    "backfill/1",
                    "backfill/2",
                }
            )
            first = harness.job(DISCOVERED, "interactive/0", behaviour=gated(gate))
            submission.submit(first)
            harness.started("interactive/0")
            harness.wait_for(lambda: len(_states()) >= 3)

            # Another pass while the first job is held must not grow either quota.
            run_pass(DISCOVERED)
            assert _states() == {
                "interactive/0": JobState.RUNNING,
                "interactive/1": JobState.QUEUED,
                "backfill/1": JobState.QUEUED,
            }
            assert RECORD.firsts() == ["interactive/0"]

            gate.set()
            harness.wait_for(lambda: not RECORD.discover)
            harness.settle()

            assert set(_states().values()) == {JobState.COMPLETED}
            assert RECORD.firsts() == [
                "interactive/0",
                "interactive/1",
                "backfill/1",
                "interactive/2",
                "backfill/2",
            ]
            assert RECORD.peak == 1
