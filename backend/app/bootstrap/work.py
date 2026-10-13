"""Compose background work for this process: catalog, engine, heartbeat.

Every process (the API, a worker, the unified image) builds the same catalog,
binds one engine, and heartbeats. What differs by role is which lanes it
listens to:

- ``all`` (unified image) and ``api`` with ``VAULT_API_RUNS_JOBS=true``
  listen to every lane;
- ``api`` with ``VAULT_API_RUNS_JOBS=false`` listens to none: it only records
  intent and nudges, and a worker executes;
- ``worker`` listens to every lane.

Every role can *submit* (nudge) work. The reconciler tick is a DBOS scheduled
workflow; its firing is unique across processes, so no role is special.
"""

from __future__ import annotations

import contextvars
import os
import threading
from dataclasses import dataclass

from app.core.config import ProcessRole, settings
from app.core.logging import get_logger
from app.db.models import LaneName
from app.modules.work import catalog as catalog_module
from app.modules.work import executors, fences
from app.modules.work.catalog import WorkCatalog
from app.modules.work.contracts import JobDefinition, JobEngine

logger = get_logger(__name__)


def definitions() -> list[JobDefinition]:
    """Every job definition, collected from the module that owns its work."""
    from app.bootstrap.optional_features import (
        inference_available,
        similarity_available,
    )
    from app.modules.administration import audit_jobs
    from app.modules.backups import gc_jobs
    from app.modules.backups import jobs as backup_jobs
    from app.modules.derivatives import jobs as derivative_jobs
    from app.modules.identity import jobs as identity_jobs
    from app.modules.ingestion import inbox, library_transfer
    from app.modules.ingestion import jobs as ingest_jobs
    from app.modules.ingestion.artifact_uploads import handoff
    from app.modules.notifications import notifications
    from app.modules.printing import jobs as printing_jobs
    from app.modules.sources import external_library
    from app.modules.storage import jobs as storage_jobs
    from app.modules.work import housekeeping

    optional: list[JobDefinition] = []
    if similarity_available():
        # An installation may ship without the similarity package at all, so
        # it is imported only once it is known to be present.
        from app.modules.similarity import jobs as similarity_jobs

        optional.extend(similarity_jobs.definitions())
    if inference_available():
        # AI Search: projection, indexing, captions, expansion, model downloads.
        from app.modules.inference import jobs as inference_jobs
        from app.modules.search import jobs as search_jobs

        optional.extend(search_jobs.definitions())
        optional.extend(inference_jobs.definitions())
    return [
        housekeeping.definition(),
        *ingest_jobs.definitions(),
        *handoff.definitions(),
        *library_transfer.definitions(),
        *inbox.definitions(),
        *derivative_jobs.definitions(),
        *optional,
        *external_library.definitions(),
        *backup_jobs.definitions(),
        *gc_jobs.definitions(),
        *audit_jobs.definitions(),
        *storage_jobs.definitions(),
        *notifications.definitions(),
        *printing_jobs.definitions(),
        *identity_jobs.definitions(),
    ]


def build_catalog() -> WorkCatalog:
    from app.db.session import get_session_factory

    catalog = WorkCatalog(definitions())
    with get_session_factory().scoped_session() as session:
        catalog.apply_overrides(session)
    return catalog


def listen_lanes() -> list[LaneName] | None:
    """The lanes this process executes; ``None`` means every lane."""
    if settings.process_role is ProcessRole.API and not settings.api_runs_jobs:
        return []
    return None


def validate_topology() -> None:
    """Refuse a split topology the configured stores cannot support.

    A worker, or an API that does not run jobs, shares work with another
    process. That needs a database every process can reach concurrently
    (PostgreSQL) and disk every process can reach, declared with
    ``VAULT_SHARED_STORAGE=true``: uploads are staged on local disk whatever
    the storage backend, and the worker that commits one reads the bytes the
    API staged. With local storage the same volume also holds the vault and
    thumbnails.
    """
    from sqlalchemy.engine import make_url

    role = settings.process_role
    split = listen_lanes() == [] or role is ProcessRole.WORKER
    if not split:
        return
    backend = make_url(settings.db_url).get_backend_name()
    if backend != "postgresql":
        raise RuntimeError(
            f"VAULT_PROCESS_ROLE={role} splits work across processes and requires "
            "PostgreSQL (VAULT_DB_URL); SQLite supports the unified and api roles"
        )
    if not settings.shared_storage:
        local_storage = str(settings.storage_backend) in {"", "local"}
        mounted = "vault, thumbnails and staging" if local_storage else "staging"
        raise RuntimeError(
            f"a split topology requires the {mounted} directories on one volume "
            "mounted by every process; declare it with VAULT_SHARED_STORAGE=true"
        )


def build_engine(catalog: WorkCatalog) -> JobEngine:
    from app.runtime.engine.dbos_engine import DbosJobEngine, system_database_url

    url, schema = system_database_url(settings.db_url)
    return DbosJobEngine(
        catalog,
        system_database_url=url,
        schema=schema,
        executor_id=executors.executor_id(),
    )


@dataclass
class WorkRuntime:
    engine: JobEngine
    catalog: WorkCatalog
    stop: threading.Event
    heartbeat: threading.Thread | None = None


_runtime: WorkRuntime | None = None
# How ``start`` was called by a process that held its work at startup,
# because an interrupted restore's maintenance still governed.
_held: dict | None = None
_held_lock = threading.Lock()


def _heartbeat_loop(stop: threading.Event) -> None:
    """Renew this executor's row and fences until the process stops."""
    from app.runtime.maintenance import active_mutations

    interval = max(1, settings.fence_heartbeat_seconds)
    while not stop.wait(interval):
        try:
            executors.heartbeat(active_mutations=active_mutations())
            fences.heartbeat(executors.executor_id())
        except Exception:  # noqa: BLE001 - the next beat retries
            logger.warning("work heartbeat failed")


def start(
    *,
    engine: JobEngine | None = None,
    catalog: WorkCatalog | None = None,
    publisher=None,
    sole_api: bool = False,
) -> WorkRuntime:
    """Bind and launch this process's engine, then reconcile everything once.

    ``publisher`` is where Job and derivative notices go (the API's event
    bus, or a worker's NOTIFY publisher); ``None`` publishes nothing.
    ``sole_api`` says this process holds the vault's API lock, so any earlier
    API process is dead and its work is rerun now rather than after the
    stale window.
    """
    global _runtime
    from app.modules.work import events
    from app.modules.work.jobs import jobs

    validate_topology()
    catalog = catalog or build_catalog()
    engine = engine or build_engine(catalog)
    lanes = listen_lanes()
    events.bind(publisher)
    jobs.clear_listeners()
    jobs.subscribe(events.job_changed)
    catalog_module.bind(engine, catalog)
    engine.launch(listen_lanes=lanes)
    executors.register(
        role=settings.process_role,
        lanes=list(catalog.lanes) if lanes is None else lanes,
    )
    if sole_api:
        retired = executors.retire_predecessors()
        if retired:
            logger.warning("rerunning the work of %d previous API process(es)", retired)
    from app.modules.work.reconciler import (
        sweep_foreign_versions,
        sweep_lost_passes,
        sweep_lost_terminal_attempts,
    )
    from app.modules.work.submission import forget_queued_passes, nudge_all

    swept = sweep_foreign_versions()
    if swept:
        logger.warning(
            "cancelled %d execution(s) of another application version", swept
        )
    # Passes a dead process was running hold the global reconcile lane; the
    # startup reconcile below could not start without freeing them.
    lost = sweep_lost_passes()
    if lost:
        logger.warning("cancelled %d reconcile pass(es) of a lost executor", lost)
    terminal = sweep_lost_terminal_attempts()
    if terminal:
        logger.warning("cancelled %d terminal attempt(s) of a lost executor", terminal)
    # Queued-pass marks may belong to passes a dead process (or the sweep
    # above) will never run; without this the startup reconcile below would
    # be suppressed until their grace expires.
    forget_queued_passes()
    nudge_all()
    stop = threading.Event()
    # In this process's context, so it heartbeats through the same session
    # factory the rest of startup used.
    heartbeat = threading.Thread(
        target=contextvars.copy_context().run,
        args=(_heartbeat_loop, stop),
        name="work-heartbeat",
        daemon=True,
    )
    heartbeat.start()
    _runtime = WorkRuntime(
        engine=engine, catalog=catalog, stop=stop, heartbeat=heartbeat
    )
    logger.info(
        "background work started role=%s executor=%s lanes=%s pid=%s",
        settings.process_role,
        executors.executor_id(),
        "all" if lanes is None else ",".join(lanes) or "none",
        os.getpid(),
    )
    return _runtime


def hold(**start_kwargs) -> None:
    """Defer ``start`` until an interrupted restore no longer governs.

    Nothing restarts the process when recovery resolves the restore, so
    whatever resolves it calls ``release_held``.
    """
    global _held
    with _held_lock:
        _held = start_kwargs


def release_held() -> bool:
    """Start the work this process held, once no restore governs it.

    Returns whether it started now. Harmless when nothing is held, when the
    work already runs, or while a restore still holds maintenance.
    """
    global _held
    from app.runtime.maintenance import restore_in_progress

    with _held_lock:
        if _held is None or _runtime is not None or restore_in_progress():
            return False
        start_kwargs, _held = _held, None
        try:
            start(**start_kwargs)
        except Exception:  # noqa: BLE001 - the resolved restore stands; retry later
            _held = start_kwargs
            logger.exception("held background work failed to start")
            return False
    logger.info("background work started after restore recovery")
    return True


def stop() -> None:
    """Stop heartbeating, shut the engine down, and forget this executor."""
    global _runtime, _held
    runtime = _runtime
    _runtime = None
    with _held_lock:
        _held = None
    if runtime is None:
        return
    runtime.stop.set()
    try:
        runtime.engine.shutdown()
    finally:
        from app.modules.work import events
        from app.modules.work.jobs import jobs

        catalog_module.bind(None, None)
        events.bind(None)
        jobs.clear_listeners()
        from app.modules.work import async_steps

        async_steps.stop()
        try:
            for name in fences.held_by(executors.executor_id()):
                fences.release(name, holder=executors.executor_id())
            executors.deregister()
        except Exception:  # noqa: BLE001 - expiry settles what shutdown could not
            logger.warning("executor cleanup failed at shutdown")


def after_restore() -> None:
    """The database was replaced: discard engine state and reconcile afresh.

    The engine's executions described Jobs of the database that no longer
    exists. Its state is discarded, the engine relaunched, and every
    definition reconciled, which resubmits whatever the restored database
    says is still owed. Jobs a restore supersedes are settled first, whether
    or not this process runs an engine: that is a fact about the database.
    A process that held its work behind an interrupted restore starts it
    now, on the restored database.
    """
    from app.modules.work.service import supersede_restored
    from app.modules.work.submission import forget_queued_passes, nudge_all

    superseded = supersede_restored()
    if superseded:
        logger.info("cancelled %d Job(s) a restore superseded", superseded)
    runtime = _runtime
    if runtime is None:
        release_held()
        return
    runtime.engine.reset()
    catalog_module.bind(runtime.engine, runtime.catalog)
    lanes = listen_lanes()
    runtime.engine.launch(listen_lanes=lanes)
    executors.register(
        role=settings.process_role,
        lanes=list(runtime.catalog.lanes) if lanes is None else lanes,
    )
    # The snapshot's "a pass is already queued" marks belong to the engine of
    # the process that took it; the reset engine will never run those passes.
    forget_queued_passes()
    nudge_all()


def current() -> WorkRuntime | None:
    return _runtime
