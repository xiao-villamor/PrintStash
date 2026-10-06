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
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dbos import (
    DBOS,
    DBOSClient,
    DBOSConfig,
    Queue,
    SetEnqueueOptions,
    SetWorkflowID,
)
from dbos import error as dbos_error
from sqlalchemy import create_engine, inspect
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
_SETTLED = ["SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"]
_PRUNE_BATCH = 500
_FIFO_INSPECTION_PAGE = 100
_FIFO_INSPECTION_LIMIT = 500
_FIFO_DRAIN_REQUIRED = (
    "derivation_queue_requires_drain: stop all work processes, drain legacy "
    "derivation queues and restart together before enabling FIFO scheduling"
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
        polling_interval_seconds: float = 1.0,
        tick_seconds: int | None = None,
    ) -> None:
        self.catalog = catalog
        self.url = system_database_url
        self.schema = schema
        self._executor_id = executor_id
        self.app_version = app_version or settings.app_version
        self.polling = polling_interval_seconds
        self.tick_seconds = tick_seconds or settings.jobs_reconcile_interval_seconds
        self._queues: dict[LaneName, Queue] = {}
        self._launched = False
        self._lock = threading.Lock()

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
            config["db_engine_kwargs"] = {"connect_args": {"timeout": 30}}
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
            self._require_fifo_queues_drained()
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

    def _require_fifo_queues_drained(self) -> None:
        """Refuse a mixed scheduling rollout before registering consumers.

        Operators must stop every old producer before this bounded snapshot;
        it cannot fence a process still submitting work under the old policy.
        Only the public client reads persisted queue state, without payloads.
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
            client = DBOSClient(
                system_database_url=self.url,
                dbos_system_schema=self.schema,
                lazy=True,
                retry_connection_errors=False,
                observability_query_timeout_sec=5,
            )
            try:
                inspected = 0
                neutral_rank = PRIORITY_RANK[WorkPriority.INTERACTIVE]
                for lane in lanes:
                    offset = 0
                    while True:
                        limit = min(
                            _FIFO_INSPECTION_PAGE, _FIFO_INSPECTION_LIMIT - inspected
                        )
                        if limit == 0:
                            raise RuntimeError("fifo_queue_inspection_limit")
                        rows = client.list_workflows(
                            queue_name=lane,
                            status=_ACTIVE,
                            limit=limit,
                            offset=offset,
                            load_input=False,
                            load_output=False,
                        )
                        if any(row.priority != neutral_rank for row in rows):
                            raise RuntimeError("fifo_queue_legacy_rank")
                        inspected += len(rows)
                        if len(rows) < limit:
                            break
                        offset += len(rows)
            finally:
                client.destroy()
        except Exception as exc:
            raise RuntimeError(_FIFO_DRAIN_REQUIRED) from exc

    def _register_queue(self, lane: Lane) -> Queue:
        options: dict[str, Any] = {
            "polling_interval_sec": self.polling,
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
