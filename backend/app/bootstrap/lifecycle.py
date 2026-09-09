"""Lifecycle."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.engine.url import make_url

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import app_info as _app_info
from app.core.topology import acquire_process_lock, release_process_lock
from app.db.session import get_session_factory, init_db
from app.modules.administration.audit import (
    install_audit_listeners,
)
from app.modules.administration.runtime_config import (
    apply_environment_storage_provider,
    apply_overlay,
    ensure_jwt_secret,
    is_configured,
)
from app.modules.backups.backup.recovery import inspect_restore_recovery
from app.modules.backups.backup_schedule import run_due_backup
from app.modules.backups.gc_planner import run_scheduled_gc
from app.modules.notifications.notifications import run_dispatcher_loop
from app.modules.printing.printer_hub import PrinterHub
from app.modules.printing.printer_jobs import (
    reconcile_stranded_dispatches,
    run_fleet_scheduler,
)
from app.modules.printing.printer_provider import (
    build_provider_registry,
    get_provider_client,
)
from app.modules.sources.library_watcher import LibraryWatcher
from app.modules.storage.storage_backend.contracts import (
    StorageBackend,
    StorageTier,
    UnavailableStorageBackend,
)
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import bind_backend
from app.modules.storage.storage_backend.s3 import S3StorageBackend
from app.runtime.maintenance import (
    begin_mutating_operation,
    end_mutating_operation,
)
from app.runtime.realtime import InProcessBus
from app.runtime.work_wakeup import LocalWorkWakeup

logger = get_logger(__name__)


async def _cancel_tasks(*tasks: asyncio.Task) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _close_outbound_clients() -> None:
    """Close pooled outbound clients while preserving the first close error."""

    from app.core.provider_redaction import redact_exception
    from app.modules.ingestion.capture_provider_transport import (
        close_provider_transport,
    )
    from app.modules.printing.moonraker import close_http_client

    try:
        await close_http_client()
    finally:
        try:
            await close_provider_transport()
        except Exception as exc:
            # A provider pool is best-effort shutdown work.  Do not prevent the
            # process lock from being released or mask a failure from the
            # existing shared HTTP client cleanup.
            logger.error(
                "failed to close capture provider transport error=%s",
                redact_exception(exc),
            )


def _safe_db_url(value: str) -> str:
    try:
        return make_url(value).render_as_string(hide_password=True)
    except Exception:
        return "<invalid-db-url>"


def _compose_storage_backend(
    *, recover_publications: bool = True, recovery_only: bool = False
) -> StorageBackend:
    """Bind the configured backend before serving requests.

    Restore maintenance is entered before startup repair.  In that state the
    adapter is still useful for reads and for the explicitly journaled restore,
    but setup probes are unsafe: they can create remote probe objects or
    mutate a mounted root while the active database is being established.
    """
    try:
        if settings.storage_provider_error:
            raise RuntimeError(settings.storage_provider_error)
        from app.modules.storage.storage_providers import provider_transport

        transport = provider_transport(str(settings.storage_backend))
        if transport in {"webdav", "sftp"}:
            from app.modules.storage.storage_opendal import OpenDALStorageBackend
            from app.modules.storage.storage_providers import (
                parse_provider_config,
                resolve_transport,
            )

            provider_config = parse_provider_config(
                json.loads(str(settings.storage_provider_config))
            )
            storage_backend = OpenDALStorageBackend(resolve_transport(provider_config))
        elif transport == "s3":
            storage_backend = S3StorageBackend(check_bucket=not recovery_only)
        elif transport == "local":
            storage_backend = LocalStorageBackend()
        else:
            raise ValueError("storage_provider_not_available_for_vault")
    except Exception as exc:
        # An explicit but invalid provider must never silently become local
        # storage. Keep the API/health surface available in recovery mode while
        # every storage mutation fails closed.
        logger.exception("selected storage provider unavailable")
        storage_backend = UnavailableStorageBackend(exc.__class__.__name__)
    if not recovery_only:
        try:
            storage_backend.ensure_setup()
        except Exception as exc:
            # Provider reachability and root probes are runtime health, not
            # process-start prerequisites. Keep the API/admin health surface
            # available with a backend that rejects every storage mutation;
            # silently falling back to local storage would split the vault.
            logger.exception("selected storage provider unavailable during setup")
            storage_backend = UnavailableStorageBackend(exc.__class__.__name__)
    if (
        not recovery_only
        and storage_backend.backend_name != "unavailable"
        and (
            storage_backend.capabilities.tier is StorageTier.UNGUARDED
            and not settings.storage_allow_unverified
            and not getattr(storage_backend, "recovery_mode", False)
        )
    ):
        raise RuntimeError(
            "unguarded storage refused; set "
            "VAULT_STORAGE_ALLOW_UNVERIFIED=true to acknowledge the risk"
        )
    logger.info(
        "storage capabilities backend=%s tier=%s identity=%s",
        storage_backend.backend_name,
        storage_backend.capabilities.tier.value,
        storage_backend.capabilities.object_identity.value,
    )
    for warning in storage_backend.capabilities.warnings:
        logger.warning("storage capability warning: %s", warning)
    bound = bind_backend(storage_backend)
    if recover_publications and storage_backend.backend_name != "unavailable":
        from app.modules.ingestion.inbox import reconcile_storage_publications

        recovered = reconcile_storage_publications()
        if recovered:
            logger.warning("reconciled %d pending storage publication(s)", recovered)
    return bound


def _prepare_storage_for_startup(
    *, recover_publications: bool = True, recovery_only: bool = False
) -> StorageBackend:
    """Bind storage and recover its durable publications before Inbox recovery."""
    backend = _compose_storage_backend(
        recover_publications=recover_publications, recovery_only=recovery_only
    )
    from app.modules.ingestion.inbox import reconcile_interrupted_items

    if recover_publications and backend.backend_name != "unavailable":
        interrupted_imports = reconcile_interrupted_items()
        if interrupted_imports:
            logger.warning(
                "reconciled %d interrupted pending import(s)", interrupted_imports
            )
    return backend


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.modules.storage.storage_paths import validate_runtime_storage_paths

    # Validate environment-time paths before even creating the process-lock
    # rendezvous beside the database.
    validate_runtime_storage_paths()
    process_lock = acquire_process_lock()
    app.state.process_lock = process_lock
    logger.info("starting %s v%s", settings.app_name, settings.app_version)
    _app_info.info({"version": settings.app_version, "name": settings.app_name})
    # Inspect the filesystem journal before opening or migrating the database.
    # A crash marker is the recovery authority; startup must not run normal
    # schema/identity/storage repairs before deciding whether it is present.
    restore_maintenance = inspect_restore_recovery()
    # DB must still exist before we can read the runtime overlay. The journal
    # decision above gates every application-owned repair after initialization.
    init_db()
    with get_session_factory().scoped_session() as session:
        apply_overlay(session)
        from app.modules.administration.runtime_config import (
            enroll_legacy_local_roots,
            ensure_legacy_s3_root,
            ensure_storage_identity,
        )

        if not restore_maintenance:
            ensure_storage_identity(session)
            # Migration covers normal upgrades; this bounded repair also
            # handles stamped/create_all legacy databases.  ``apply_overlay``
            # above already projects the historical literal during
            # recovery-only startup without persisting while the journal is
            # authoritative.
            ensure_legacy_s3_root(session)
            enroll_legacy_local_roots(session)
            apply_environment_storage_provider(session)
            # Persisted runtime configuration can differ from environment
            # defaults, so revalidate before creating the secrets key.
            validate_runtime_storage_paths()
            # Must run after apply_overlay: that call clears the overlay dict.
            ensure_jwt_secret(session)
            # Clear any NAS scans stranded RUNNING by a previous unclean
            # shutdown, otherwise the scheduler would skip them forever.
            from app.modules.sources.external_library import reset_orphaned_scans

            reset_count = reset_orphaned_scans(session)
            if reset_count:
                logger.warning(
                    "reset %d external library scan(s) stranded by restart",
                    reset_count,
                )
        configured = is_configured(session)
    if restore_maintenance:
        logger.critical(
            "interrupted restore detected; application remains in restore maintenance"
        )
    # Storage must be configured and bound before either publication recovery
    # or Inbox recovery can inspect durable capture-slot receipts.
    _backend = _prepare_storage_for_startup(
        recover_publications=not restore_maintenance,
        recovery_only=restore_maintenance,
    )
    from app.modules.administration.artifact_cache_config import (
        live_policy,
        validate_cache_root,
    )
    from app.modules.storage.artifact_materializer import ArtifactMaterializer
    from app.modules.storage.capacity import CapacityManager, CapacityResource
    from app.modules.storage.materializer_runtime import bind_materializer

    try:
        capacity = CapacityManager(get_session_factory())
        bind_materializer(
            ArtifactMaterializer(
                validate_cache_root(settings.artifact_cache_root),
                live_policy,
                reserve=lambda token, root, size: capacity.hold(
                    f"cache:{token}",
                    [CapacityResource.for_path(root, size, role="cache")],
                ),
                recover_reservation=lambda token: capacity.release(f"cache:{token}"),
            )
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        logger.exception("artifact cache unavailable; source reads remain enabled")
        bind_materializer(None, configured_root=Path(settings.artifact_cache_root))
    from app.runtime.jobs import reconcile_interrupted_jobs

    interrupted_jobs = reconcile_interrupted_jobs() if not restore_maintenance else 0
    if interrupted_jobs:
        logger.warning("reconciled %d interrupted background job(s)", interrupted_jobs)
    if not restore_maintenance:
        from app.modules.ingestion.artifact_uploads import reconcile_artifact_uploads

        upload_recovery = reconcile_artifact_uploads()
        if upload_recovery.reconciled or upload_recovery.expired:
            logger.warning(
                "reconciled %d and expired %d artifact upload session(s)",
                upload_recovery.reconciled,
                upload_recovery.expired,
            )
    from app.modules.administration.vault_audit import reconcile_interrupted_runs

    interrupted_audits = reconcile_interrupted_runs() if not restore_maintenance else 0
    if interrupted_audits:
        logger.warning("reconciled %d interrupted vault audit(s)", interrupted_audits)
    stranded_dispatches = (
        reconcile_stranded_dispatches() if not restore_maintenance else 0
    )
    if stranded_dispatches:
        logger.warning("reconciled %d stranded fleet dispatch(es)", stranded_dispatches)
    if not configured:
        logger.info("vault is unconfigured; browser setup mode=%s", settings.setup_mode)
    logger.info(
        "backend=%s data_dir=%s thumb_dir=%s db=%s",
        settings.storage_backend,
        settings.data_dir,
        settings.thumb_dir,
        _safe_db_url(settings.db_url),
    )
    install_audit_listeners()
    printer_provider_registry = build_provider_registry()
    app.state.printer_provider_registry = printer_provider_registry
    provider_builder = partial(get_provider_client, registry=printer_provider_registry)
    hub = PrinterHub(
        InProcessBus(),
        session_factory=get_session_factory(),
        provider_builder=provider_builder,
    )
    app.state.printer_hub = hub
    watcher = LibraryWatcher()
    app.state.library_watcher = watcher
    work_wakeup = LocalWorkWakeup()
    app.state.work_wakeup = work_wakeup
    app.state.gc_task = asyncio.create_task(
        _gc_loop(storage_maintenance_enabled=configured)
    )
    app.state.external_scan_task = asyncio.create_task(_external_scan_loop())
    app.state.automatic_backup_task = asyncio.create_task(_automatic_backup_loop())
    from app.runtime.audit_scheduler import run_audit_scheduler

    app.state.audit_scheduler_task = asyncio.create_task(run_audit_scheduler())
    app.state.notification_task = asyncio.create_task(run_dispatcher_loop())
    app.state.fleet_scheduler_task = asyncio.create_task(
        run_fleet_scheduler(work_wakeup, provider_builder)
    )
    await hub.start_all()
    # Real-time folder watching is best-effort: never let it block startup.
    try:
        await watcher.start_all()
    except Exception:
        logger.exception("library watcher failed to start; scheduled scans still run")
    yield
    bind_materializer(None)
    logger.info("shutting down printer hub")
    await _cancel_tasks(
        app.state.gc_task,
        app.state.external_scan_task,
        app.state.automatic_backup_task,
        app.state.audit_scheduler_task,
        app.state.notification_task,
        app.state.fleet_scheduler_task,
    )
    await watcher.stop_all()
    await hub.stop_all()
    try:
        await _close_outbound_clients()
    finally:
        release_process_lock(process_lock)
    logger.info("shutting down")


async def _gc_loop(*, storage_maintenance_enabled: bool = True) -> None:
    # Run once at startup (not sleep-first): a container that lives less than
    # an hour — frequent redeploys, dev — would otherwise never GC expired
    # trash or prune old notification deliveries.
    while True:
        if not storage_maintenance_enabled:
            await asyncio.sleep(3600)
            continue
        if not begin_mutating_operation():
            await asyncio.sleep(1)
            continue
        try:
            try:
                # Sync DB + storage I/O — keep it off the event loop.
                await asyncio.to_thread(run_scheduled_gc)
            except Exception:
                logger.exception("scheduled GC failed")
            try:
                from app.modules.storage.storage_inventory import (
                    refresh_inventory_sample,
                )

                await asyncio.to_thread(refresh_inventory_sample, get_session_factory())
            except Exception:
                logger.exception("storage inventory sampling failed")
            try:
                from app.modules.notifications.notifications import prune_deliveries

                await asyncio.to_thread(prune_deliveries)
            except Exception:
                logger.exception("notification delivery pruning failed")
            try:
                from app.modules.ingestion.inbox import (
                    prune_expired_browser_leases,
                    prune_history,
                )

                await asyncio.to_thread(prune_history)
                await asyncio.to_thread(prune_expired_browser_leases)
            except Exception:
                logger.exception("pending import history pruning failed")
            try:
                from app.modules.identity.auth import prune_expired_refresh_tokens

                await asyncio.to_thread(prune_expired_refresh_tokens)
            except Exception:
                logger.exception("expired refresh-token pruning failed")
            try:
                from app.modules.ingestion.artifact_uploads import (
                    reconcile_artifact_uploads,
                )

                await asyncio.to_thread(reconcile_artifact_uploads)
            except Exception:
                logger.exception("artifact upload reconciliation failed")
        finally:
            end_mutating_operation()
        await asyncio.sleep(3600)


async def _external_scan_loop() -> None:
    """Poll enabled external (NAS) libraries and scan those whose interval elapsed.

    No-op while the ``external_libraries_enabled`` opt-in is off. All blocking DB
    and filesystem work runs in a worker thread to keep the event loop free.
    """
    while True:
        await asyncio.sleep(60)
        if not begin_mutating_operation():
            continue
        try:
            await asyncio.to_thread(_run_due_external_scans)
        except Exception:
            logger.exception("external library scan tick failed")
        finally:
            end_mutating_operation()


async def _automatic_backup_loop() -> None:
    """Create at most one configured automatic backup for each UTC day."""
    while True:
        await asyncio.sleep(60)
        if not begin_mutating_operation():
            continue
        try:
            try:
                await asyncio.to_thread(run_due_backup)
            except Exception:
                logger.exception("automatic backup failed")
        finally:
            end_mutating_operation()


def _run_due_external_scans() -> None:
    from app.modules.administration.runtime_config import external_libraries_enabled
    from app.modules.sources import external_library

    with get_session_factory().scoped_session() as session:
        if not external_libraries_enabled(session):
            return
        due = external_library.libraries_due_for_scan(session)
    for library_id in due:
        try:
            external_library.scan_library(library_id)
        except Exception:
            logger.exception("scheduled scan failed for library %s", library_id)
