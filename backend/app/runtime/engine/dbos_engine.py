"""The DBOS adapter: the only module that imports ``dbos``.

Lanes become DBOS queues; a Job attempt becomes one workflow whose inputs are
``(job_id, attempt)`` and whose steps are the definition's steps, run through
``DBOS.run_step`` so each is checkpointed and retried by DBOS; a reconcile pass
is one more workflow on the ``reconcile`` queue; the reconciler tick is the
only persistently scheduled workflow. ``execution_id`` is the DBOS workflow id and
``dedupe_key`` its deduplication id.

System state lives outside the application's tables: a sibling SQLite file of
the application database, or the ``dbos`` schema of the same PostgreSQL
database. It is disposable. A restore deletes it and the reconciler rebuilds
every in-flight Job from the application database.
"""

from __future__ import annotations

import asyncio
import contextvars
import sqlite3
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dbos import (
    DBOS,
    DBOSConfig,
    Queue,
    SetEnqueueOptions,
    SetWorkflowID,
)
from dbos import error as dbos_error
from sqlalchemy import bindparam, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import DropSchema

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import JobKind, LaneName, WorkPriority
from app.modules.work.catalog import WorkCatalog
from app.modules.work.contracts import (
    ActiveExecution,
    Deduplicated,
    EngineEvidence,
    EngineStatus,
    ExecutionKind,
    JobEngine,
    JobSubmission,
    Lane,
    LaneDepth,
    LaneOrder,
    LanePace,
    PassSubmission,
    RetryPolicy,
    Submission,
    SubmitOutcome,
)
from app.modules.work.sources import interval_cron
from app.modules.work.submission import PRIORITY_RANK

logger = get_logger(__name__)

JOB_WORKFLOW = "printstash.job"
RECONCILE_WORKFLOW = "printstash.reconcile"
TICK_WORKFLOW = "printstash.tick"

_STATUS = {
    "ENQUEUED": EngineStatus.QUEUED,
    "DELAYED": EngineStatus.DELAYED,
    "PENDING": EngineStatus.RUNNING,
    "SUCCESS": EngineStatus.SUCCEEDED,
    "ERROR": EngineStatus.FAILED,
    "MAX_RECOVERY_ATTEMPTS_EXCEEDED": EngineStatus.FAILED,
    "CANCELLED": EngineStatus.CANCELLED,
}
_ACTIVE = ["ENQUEUED", "DELAYED", "PENDING"]
_WAITING = ["ENQUEUED", "DELAYED"]
_SETTLED = ["SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"]
_PRUNE_BATCH = 500
_FIFO_ADOPTION_FAILED = (
    "derivation_queue_unreadable: the background-work engine database could not "
    "be read to adopt waiting derivation work. Check that it is readable and "
    "that no other PrintStash process holds it, then start again."
)
_ENGINE_DATABASE_UNMIGRATABLE = (
    "engine_database_unmigratable: the background-work engine database was "
    "created by a build that predates its migration table, so it cannot be "
    "upgraded in place. Stop every PrintStash process, move the engine database "
    "aside (printstash-dbos.sqlite beside the application database, or the engine "
    "schema on PostgreSQL) and start again. Unfinished Jobs replay from the "
    "application database; only engine checkpoints are lost."
)


_client_pool: ThreadPoolExecutor | None = None
_client_pool_lock = threading.Lock()


def _off_loop(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    global _client_pool
    with _client_pool_lock:
        if _client_pool is None:
            _client_pool = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="dbos-client"
            )
    return _client_pool.submit(fn, *args, **kwargs).result()


def _detached(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call the DBOS client API as a caller outside any workflow would.

    Two callers need this. A route handler nudges from the event loop's
    thread, where DBOS's synchronous API refuses to run; the call moves to a
    worker thread, which also starts with an empty context. And submissions
    happen inside executions: a reconcile pass submits the Jobs it found from
    within its step, and a Job's step nudges other sources. DBOS treats a
    workflow started from inside a workflow as its child, and refuses one
    started from inside a step. Ours are independent top-level executions
    keyed by their own ids, so the call runs in an empty context, where DBOS
    sees no enclosing workflow.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return _off_loop(fn, *args, **kwargs)
    if DBOS.workflow_id is None:
        return fn(*args, **kwargs)
    return contextvars.Context().run(fn, *args, **kwargs)


def system_database_url(db_url: str) -> tuple[str, str | None]:
    """The engine's system database for an application database URL.

    SQLite: ``printstash-dbos.sqlite`` beside the application file, in the same
    volume, so no new setting or mount exists. PostgreSQL: the same database,
    isolated in the ``dbos`` schema. Returns ``(url, schema)``.
    """
    url = make_url(db_url)
    if url.get_backend_name() == "sqlite":
        database = url.database or ""
        if database in {"", ":memory:"}:
            raise ValueError("engine_requires_file_backed_sqlite")
        path = Path(database)
        sibling = path.with_name(f"{path.stem}-dbos{path.suffix or '.sqlite'}")
        return f"sqlite:///{sibling}", None
    return url.set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    ), "dbos"


_SQLITE_BUSY_SECONDS = 30


def _sqlite_system_connection(url: str) -> Callable[[], sqlite3.Connection]:
    """Open the engine's SQLite file the way the application opens its own.

    DBOS leaves its file in rollback-journal mode with full synchronisation and
    begins every transaction IMMEDIATE, so each queue poll and checkpoint
    excluded every other one and forced the journal to disk. Write-ahead
    logging lets its polls read while a checkpoint commits. The engine's copy
    is disposable: the reconciler rebuilds lost executions from Job rows.
    """
    database = make_url(url).database
    if database is None:
        raise ValueError("engine_requires_file_backed_sqlite")
    synchronous = settings.sqlite_synchronous.upper()
    if synchronous not in {"NORMAL", "FULL"}:
        raise ValueError("sqlite_synchronous must be NORMAL or FULL")

    def connect() -> sqlite3.Connection:
        # The timeout waits out a locked file instead of failing the checkpoint.
        connection = sqlite3.connect(
            database, timeout=_SQLITE_BUSY_SECONDS, check_same_thread=False
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"PRAGMA synchronous={synchronous}")
        return connection

    return connect


class _DbosRunner:
    def run(self, name: str, fn: Callable[[], Any], retry: RetryPolicy) -> Any:
        options: dict[str, Any] = {"name": name}
        if retry.max_attempts > 1:
            options.update(
                retries_allowed=True,
                max_attempts=retry.max_attempts,
                interval_seconds=retry.interval_seconds,
                backoff_rate=retry.backoff_rate,
                should_retry=retry.should_retry,
            )
        try:
            return DBOS.run_step(options, fn)  # type: ignore[arg-type]
        except dbos_error.DBOSMaxStepRetriesExceeded as exc:
            # Surface the step's own last error, not DBOS's wrapper, so the
            # Job records why the work failed rather than how often.
            errors = getattr(exc, "errors", None) or []
            if errors:
                raise errors[-1] from exc
            raise

    def sleep(self, seconds: float) -> None:
        DBOS.sleep(seconds)

    def is_cancellation(self, error: BaseException) -> bool:
        return isinstance(error, dbos_error.DBOSWorkflowCancelledError)


class DbosJobEngine(JobEngine):
    """Background work on DBOS Transact."""

    def __init__(
        self,
        catalog: WorkCatalog,
        *,
        system_database_url: str,
        schema: str | None,
        executor_id: str,
        app_version: str | None = None,
        polling_interval_seconds: float | None = None,
        tick_seconds: int | None = None,
    ) -> None:
        self.catalog = catalog
        self.url = system_database_url
        self.schema = schema
        self._executor_id = executor_id
        self.app_version = app_version or settings.app_version
        self.polling = (
            settings.jobs_queue_poll_interval_seconds
            if polling_interval_seconds is None
            else polling_interval_seconds
        )
        self.tick_seconds = tick_seconds or settings.jobs_reconcile_interval_seconds
        self._queues: dict[LaneName, Queue] = {}
        self._launched = False
        self._lock = threading.Lock()
        # Lanes polled at the boosted interval, with when each returns to its own.
        self._boosted: dict[LaneName, float] = {}
        self._boost_lock = threading.Lock()
        self._relax_timer: threading.Timer | None = None

    @property
    def executor_id(self) -> str:
        return self._executor_id

    # -- lifecycle -----------------------------------------------------------

    def _config(self) -> DBOSConfig:
        config: DBOSConfig = {
            "name": "printstash",
            "system_database_url": self.url,
            "application_version": self.app_version,
            "executor_id": self._executor_id,
            "notification_listener_polling_interval_sec": min(self.polling, 1.0),
            "scheduler_polling_interval_sec": min(self.polling * 10, 30.0),
            "log_level": "WARNING",
        }
        if self.schema is not None:
            config["dbos_system_schema"] = self.schema
        else:
            config["db_engine_kwargs"] = {
                "creator": _sqlite_system_connection(self.url)
            }
        return config

    def _register_workflows(self) -> None:
        runner = _DbosRunner()

        @DBOS.workflow(name=JOB_WORKFLOW)
        def job_workflow(
            job_id: str, attempt: int, execution_epoch: str | None = None
        ) -> None:
            from app.modules.work.runner import execute_job

            execute_job(job_id, attempt, runner, execution_epoch=execution_epoch)

        @DBOS.workflow(name=RECONCILE_WORKFLOW)
        def reconcile_workflow(source: str) -> None:
            from app.modules.work.reconciler import execute_pass

            execute_pass(JobKind(source), runner)

        @DBOS.workflow(name=TICK_WORKFLOW)
        def tick_workflow(scheduled: datetime, context: Any) -> None:
            del scheduled, context
            from app.modules.work.reconciler import tick

            DBOS.run_step({"name": "work.tick"}, tick)

        self._job_workflow = job_workflow
        self._reconcile_workflow = reconcile_workflow
        self._tick_workflow = tick_workflow

    def launch(self, *, listen_lanes: Sequence[LaneName] | None) -> None:
        with self._lock:
            if self._launched:
                return
            self._require_migratable_system_database()
            self._adopt_fifo_queues()
            DBOS.destroy(destroy_registry=True)
            DBOS(config=self._config())
            self._register_workflows()
            if listen_lanes is not None:
                DBOS.listen_queues([lane.value for lane in listen_lanes])
            DBOS.launch()
            for lane in self.catalog.lanes.values():
                self._queues[lane.name] = self._register_queue(lane)
            # Upsert one durable schedule shared by API and worker processes.
            # Reapplying updates its cadence; restore recreates it after reset.
            DBOS.apply_schedules(
                [
                    {
                        "schedule_name": TICK_WORKFLOW,
                        "workflow_fn": self._tick_workflow,
                        "schedule": _tick_cron(self.tick_seconds),
                        "automatic_backfill": False,
                    }
                ]
            )
            self._launched = True

    def _fresh_system_database(self) -> bool:
        url = make_url(self.url)
        if url.get_backend_name() == "sqlite":
            if url.database is None:
                raise ValueError("engine_requires_file_backed_sqlite")
            try:
                return Path(url.database).stat().st_size == 0
            except FileNotFoundError:
                return True
        if self.schema is None:
            raise ValueError("engine_requires_system_schema")
        database = create_engine(self.url, connect_args={"connect_timeout": 5})
        try:
            return not inspect(database).has_schema(self.schema)
        finally:
            database.dispose()

    def _require_migratable_system_database(self) -> None:
        """Name a system database DBOS cannot migrate instead of crashing in it.

        DBOS applies its schema steps in order from the version recorded in
        ``dbos_migrations``. A database that has workflow rows but no such record
        comes from an older engine; replaying the first step then fails on a table
        that already exists, with no hint what to do.
        """
        if self._fresh_system_database():
            return
        schema = (
            None if make_url(self.url).get_backend_name() == "sqlite" else self.schema
        )
        database = create_engine(self.url)
        try:
            tables = inspect(database)
            legacy = tables.has_table("workflow_status", schema=schema) and not (
                tables.has_table("dbos_migrations", schema=schema)
            )
        finally:
            database.dispose()
        if legacy:
            raise RuntimeError(_ENGINE_DATABASE_UNMIGRATABLE)

    def _adopt_fifo_queues(self) -> None:
        """Give work queued under the previous scheduling policy the neutral rank.

        Derivation lanes dispatch in arrival order now. A database that still
        holds waiting work with the previous version's rank would otherwise be
        ordered by a policy no new Job is created under. The Job rows in the
        application database stay the record of the work and keep their own
        priority; only the engine's waiting copy is re-ranked, so nothing is
        dropped and an operator never has to drain by hand. Work already handed
        to a worker is left alone: its rank no longer orders anything.

        This reads and writes the columns every engine schema has. The system
        database is still the previous version's here (DBOS migrates it when it
        launches, after this), so its own client would select columns that do not
        exist yet and fail every existing install.
        """
        lanes = [
            lane.name.value
            for lane in self.catalog.lanes.values()
            if lane.queue_order is LaneOrder.FIFO
        ]
        if not lanes:
            return
        try:
            if self._fresh_system_database():
                return
        except Exception as exc:
            raise RuntimeError(_FIFO_ADOPTION_FAILED) from exc
        statement = text(
            f"UPDATE {self._workflow_status_table()} SET priority = :neutral "
            "WHERE queue_name IN :lanes AND status IN :waiting "
            "AND priority <> :neutral"
        ).bindparams(
            bindparam("lanes", expanding=True), bindparam("waiting", expanding=True)
        )
        connect_args = (
            {"connect_timeout": 5}
            if make_url(self.url).get_backend_name() != "sqlite"
            else {}
        )
        database = create_engine(self.url, connect_args=connect_args)
        try:
            with database.begin() as connection:
                adopted = connection.execute(
                    statement,
                    {
                        "neutral": PRIORITY_RANK[WorkPriority.INTERACTIVE],
                        "lanes": lanes,
                        "waiting": _WAITING,
                    },
                ).rowcount
        except Exception as exc:
            raise RuntimeError(_FIFO_ADOPTION_FAILED) from exc
        finally:
            database.dispose()
        if adopted:
            logger.info("re-ranked queued derivation work", extra={"count": adopted})

    def _workflow_status_table(self) -> str:
        if make_url(self.url).get_backend_name() == "sqlite":
            return "workflow_status"
        if self.schema is None:
            raise ValueError("engine_requires_system_schema")
        quote = make_url(self.url).get_dialect()().identifier_preparer.quote_schema
        return f"{quote(self.schema)}.workflow_status"

    def _interval(self, lane: Lane) -> float:
        """How often ``lane`` is polled when nothing has just been handed to it."""
        return (
            self.polling
            if lane.pace is LanePace.PROMPT
            else max(self.polling, settings.jobs_relaxed_queue_poll_interval_seconds)
        )

    def _boost(self, name: LaneName) -> None:
        """Poll ``name`` faster for a short while. Never raises into a submission.

        The engine only finds queued work by polling, so a Job someone waits on
        sits half a polling interval on average. Polling every lane that often
        would be paid for all day by an idle server; boosting the one lane a
        request just dispatched to is paid for only while a batch arrives.
        """
        window = settings.jobs_queue_boost_seconds
        lane = self.catalog.lanes[name]
        boosted = min(
            self._interval(lane), settings.jobs_boosted_queue_poll_interval_seconds
        )
        if window <= 0 or boosted >= self._interval(lane):
            return
        with self._boost_lock:
            already = name in self._boosted
            self._boosted[name] = time.monotonic() + window
            if already:
                return
            try:
                self._queues[name].set_polling_interval_sec(boosted)
            except Exception:  # noqa: BLE001 - the lane keeps its own interval
                del self._boosted[name]
                logger.exception("lane boost failed", extra={"lane": name.value})
                return
            if self._relax_timer is None:
                self._arm_relax(window)

    def _arm_relax(self, delay: float) -> None:
        timer = threading.Timer(max(delay, 0.01), self._relax)
        timer.daemon = True
        timer.name = "work-lane-relax"
        self._relax_timer = timer
        timer.start()

    def _relax(self) -> None:
        """Return lanes whose boost has run out to their own interval."""
        with self._boost_lock:
            self._relax_timer = None
            now = time.monotonic()
            for name, until in list(self._boosted.items()):
                if until > now:
                    continue
                queue = self._queues.get(name)
                try:
                    if queue is not None:
                        queue.set_polling_interval_sec(
                            self._interval(self.catalog.lanes[name])
                        )
                except Exception:  # noqa: BLE001 - tried again at the next expiry
                    logger.exception("lane relax failed", extra={"lane": name.value})
                    self._boosted[name] = now + 1.0
                    continue
                del self._boosted[name]
            if self._boosted:
                self._arm_relax(min(self._boosted.values()) - now)

    def boosted_lanes(self) -> frozenset[LaneName]:
        """Lanes currently polled at the boosted interval."""
        with self._boost_lock:
            return frozenset(self._boosted)

    def _register_queue(self, lane: Lane) -> Queue:
        options: dict[str, Any] = {
            "polling_interval_sec": self._interval(lane),
            "on_conflict": "always_update",
        }
        if lane.partitioned:
            options["partition_concurrency"] = lane.concurrency
            if lane.rate_limit is not None:
                limit, period = lane.rate_limit
                options["partition_limiter"] = {"limit": limit, "period": period}
        elif lane.scope == "global":
            options["global_concurrency"] = lane.concurrency
        else:
            options["worker_concurrency"] = lane.concurrency
        if lane.rate_limit is not None and not lane.partitioned:
            limit, period = lane.rate_limit
            options["limiter"] = {"limit": limit, "period": period}
        # The application database owns concurrency (including overrides), so
        # every launch writes it: the engine's copy is disposable.
        return DBOS.register_queue(lane.name.value, **options)

    def shutdown(self) -> None:
        with self._lock:
            if not self._launched:
                return
            with self._boost_lock:
                if self._relax_timer is not None:
                    self._relax_timer.cancel()
                    self._relax_timer = None
                self._boosted.clear()
            DBOS.destroy(destroy_registry=True, workflow_completion_timeout_sec=10)
            self._queues.clear()
            self._launched = False

    # -- submission ----------------------------------------------------------

    def submit(self, submission: Submission) -> SubmitOutcome:
        return _detached(self._submit, submission)

    def _submit(self, submission: Submission) -> SubmitOutcome:
        queue = self._queues[submission.lane]
        if DBOS.get_workflow_status(submission.execution_id) is not None:
            return SubmitOutcome.EXISTING
        # Equal engine ranks preserve FIFO without changing the Job's logical priority.
        rank = (
            PRIORITY_RANK[WorkPriority.INTERACTIVE]
            if self.catalog.lanes[submission.lane].queue_order is LaneOrder.FIFO
            else PRIORITY_RANK[submission.priority]
        )
        options: dict[str, Any] = {"priority": rank}
        if isinstance(submission, JobSubmission):
            if isinstance(submission.routing, Deduplicated):
                options["deduplication_id"] = submission.routing.key
            else:
                options["queue_partition_key"] = submission.routing.key
        if submission.delay_seconds:
            options["delay_seconds"] = submission.delay_seconds
        try:
            with SetWorkflowID(submission.execution_id), SetEnqueueOptions(**options):
                if isinstance(submission, PassSubmission):
                    queue.enqueue(self._reconcile_workflow, submission.source.value)
                else:
                    queue.enqueue(
                        self._job_workflow,
                        submission.job_id,
                        submission.attempt,
                        submission.execution_epoch,
                    )
        except dbos_error.DBOSQueueDeduplicatedError:
            return SubmitOutcome.DEDUPLICATED
        if isinstance(submission, JobSubmission) and submission.expedite:
            self._boost(submission.lane)
        return SubmitOutcome.ACCEPTED

    def cancel(self, execution_id: str) -> None:
        _detached(DBOS.cancel_workflow, execution_id)

    # -- evidence ------------------------------------------------------------

    def evidence(self, execution_ids: Sequence[str]) -> dict[str, EngineEvidence]:
        return _detached(self._evidence, execution_ids)

    def _evidence(self, execution_ids: Sequence[str]) -> dict[str, EngineEvidence]:
        found: dict[str, EngineEvidence] = {}
        ids = list(execution_ids)
        for offset in range(0, len(ids), _PRUNE_BATCH):
            chunk = ids[offset : offset + _PRUNE_BATCH]
            for status in DBOS.list_workflows(
                workflow_ids=chunk, load_input=False, load_output=False
            ):
                found[status.workflow_id] = EngineEvidence(
                    _STATUS[str(status.status)],
                    status.app_version,
                    status.executor_id,
                )
        return found

    def active(self) -> list[ActiveExecution]:
        return _detached(self._active)

    def _active(self) -> list[ActiveExecution]:
        return [
            ActiveExecution(
                execution_id=status.workflow_id,
                kind=ExecutionKind.PASS
                if status.name == RECONCILE_WORKFLOW
                else ExecutionKind.JOB,
                status=_STATUS[str(status.status)],
                app_version=status.app_version,
                executor_id=status.executor_id,
            )
            for status in DBOS.list_workflows(
                status=_ACTIVE,
                name=[JOB_WORKFLOW, RECONCILE_WORKFLOW],
                load_input=False,
                load_output=False,
            )
        ]

    def lane_depth(self, lane: LaneName) -> LaneDepth:
        return _detached(self._lane_depth, lane)

    def _lane_depth(self, lane: LaneName) -> LaneDepth:
        queued = running = 0
        for status in DBOS.list_workflows(
            queue_name=lane.value, status=_ACTIVE, load_input=False, load_output=False
        ):
            if str(status.status) == "PENDING":
                running += 1
            else:
                queued += 1
        return LaneDepth(queued=queued, running=running)

    def set_lane_concurrency(self, lane: LaneName, concurrency: int) -> None:
        queue = self._queues[lane]
        definition = self.catalog.lanes[lane]
        if definition.partitioned:
            queue.set_partition_concurrency(concurrency)
        elif definition.scope == "global":
            queue.set_global_concurrency(concurrency)
        else:
            queue.set_worker_concurrency(concurrency)

    def foreign_version_executions(self) -> list[str]:
        return [
            execution.execution_id
            for execution in self.active()
            if execution.app_version and execution.app_version != self.app_version
        ]

    def prune_history(self, *, older_than: datetime) -> int:
        return _detached(self._prune_history, older_than)

    def _prune_history(self, older_than: datetime) -> int:
        cutoff = older_than.astimezone(timezone.utc).isoformat()
        removed = 0
        while True:
            batch = [
                status.workflow_id
                for status in DBOS.list_workflows(
                    status=_SETTLED,
                    end_time=cutoff,
                    limit=_PRUNE_BATCH,
                    load_input=False,
                    load_output=False,
                )
            ]
            if not batch:
                return removed
            DBOS.delete_workflows(batch)
            removed += len(batch)
            if len(batch) < _PRUNE_BATCH:
                return removed

    def reset(self) -> None:
        """Discard the system database. The engine must be relaunched after."""
        was_launched = self._launched
        self.shutdown()
        if make_url(self.url).get_backend_name() == "sqlite":
            DBOS.reset_system_database(system_database_url=self.url)
        else:
            if self.schema is None:
                raise ValueError("engine_requires_system_schema")
            # DBOS's destructive reset drops the entire PostgreSQL database,
            # including authoritative application tables. Only our system
            # schema is disposable. Transactional DDL propagates any failure.
            database = create_engine(self.url, connect_args={"connect_timeout": 5})
            try:
                with database.begin() as connection:
                    connection.execute(
                        DropSchema(self.schema, cascade=True, if_exists=True)
                    )
            finally:
                database.dispose()
        if was_launched:
            logger.warning("engine state discarded; relaunch required")


def _tick_cron(seconds: int) -> str:
    """The tick as a cron: seconds-granular when the interval needs it."""
    if seconds % 60 == 0:
        return interval_cron(seconds)
    return f"*/{max(1, min(seconds, 59))} * * * * *"
