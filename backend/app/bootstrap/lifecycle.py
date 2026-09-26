"""Process lifecycle: prepare state, then serve (the API) or execute (a worker).

``prepare_process`` is everything a process needs before it may touch the
vault, and it is shared by the API lifespan and the worker entrypoint. What
only the API does is serve HTTP and supervise live connections (the printer
hub, the library watcher). Background work is composed in ``bootstrap.work``
and is the same in every process; nothing here starts a loop.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.engine.url import make_url

from app.core.config import ProcessRole, settings
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
from app.modules.printing.printer_hub import PrinterHub
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

logger = get_logger(__name__)


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
            await asyncio.to_thread(close_inference)
        except Exception:
            logger.error("failed to close inference transport")
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
    """Bind the configured backend before serving requests or running jobs.

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
            storage_backend.probe_staging(Path(settings.staging_dir))
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
    storage_backend.report_capability_warnings()
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


@dataclass(frozen=True)
class PreparedProcess:
    backend: StorageBackend
    configured: bool
    restore_maintenance: bool


def prepare_process(*, owner: bool) -> PreparedProcess:
    """Bring this process to the point where it may touch the vault.

    ``owner`` is the process that holds the vault's single API lock. Only the
    owner migrates the schema and runs startup repairs (identity, legacy
    roots, the JWT secret) and publication recovery; a worker binds the same
    configuration and storage without mutating any of it, so two processes
    never race on a repair.
    """
    from app.modules.storage.migration_journal import inspect_before_writes
    from app.modules.storage.storage_paths import validate_runtime_storage_paths

    # Inspect the filesystem journal before opening or migrating the database.
    # A crash marker is the recovery authority; startup must not run normal
    # schema/identity/storage repairs before deciding whether it is present.
    migration_maintenance = inspect_before_writes() if owner else False
    restore_maintenance = (
        inspect_restore_recovery() or migration_maintenance if owner else False
    )
    if owner and not migration_maintenance:
        init_db()
    with get_session_factory().scoped_session() as session:
        apply_overlay(session)
        from app.modules.administration.runtime_config import (
            enroll_legacy_local_roots,
            ensure_legacy_s3_root,
            ensure_storage_identity,
        )

        if owner and not restore_maintenance:
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
        configured = is_configured(session)
    if not restore_maintenance and not configured:
        # Its own session: first ownership opens a write-locking transaction,
        # which must not start inside the repairs above.
        from app.modules.administration.setup_bootstrap import (
            provision_from_environment,
        )

        with get_session_factory().scoped_session() as session:
            configured = provision_from_environment(session) is not None
    if restore_maintenance:
        logger.critical(
            "interrupted restore detected; application remains in restore maintenance"
        )
    # Storage must be configured and bound before either publication recovery
    # or Inbox recovery can inspect durable capture-slot receipts.
    backend = _prepare_storage_for_startup(
        recover_publications=owner and not restore_maintenance,
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
        if restore_maintenance:
            bind_materializer(None, configured_root=Path(settings.artifact_cache_root))
        else:
            capacity = CapacityManager(get_session_factory())
            bind_materializer(
                ArtifactMaterializer(
                    validate_cache_root(settings.artifact_cache_root),
                    live_policy,
                    reserve=lambda token, root, size: capacity.hold(
                        f"cache:{token}",
                        [CapacityResource.for_path(root, size, role="cache")],
                    ),
                    recover_reservation=lambda token: capacity.release(
                        f"cache:{token}"
                    ),
                )
            )
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        logger.exception("artifact cache unavailable; source reads remain enabled")
        bind_materializer(None, configured_root=Path(settings.artifact_cache_root))

    from app.db.models import VaultGeneration
    from app.modules.storage.storage_backend import generations

    with get_session_factory().scoped_session() as session:
        generation = session.get(VaultGeneration, 1)
        if generation is not None:
            with generations.activation():
                generations.publish(backend, generation.epoch)
    from app.modules.storage.vault_migration import record_first_destination_write
    from app.runtime.maintenance import observe_mutations

    observe_mutations(record_first_destination_write)
    if restore_maintenance:
        from app.runtime.maintenance import hold_restore_maintenance

        hold_restore_maintenance()
    if not configured:
        from app.modules.administration import setup_policy

        # The resolved policy, not VAULT_SETUP_MODE: a misconfigured mode keeps
        # every door shut whatever it says.
        logger.info(
            "vault is unconfigured; first-run setup=%s",
            setup_policy.label(setup_policy.current()),
        )
    logger.info(
        "role=%s backend=%s data_dir=%s thumb_dir=%s db=%s",
        settings.process_role,
        settings.storage_backend,
        settings.data_dir,
        settings.thumb_dir,
        _safe_db_url(settings.db_url),
    )
    install_audit_listeners()
    return PreparedProcess(
        backend=backend,
        configured=configured,
        restore_maintenance=restore_maintenance,
    )


SearchBindings = tuple[object, object]


def bind_search() -> SearchBindings:
    """Bind library search and its projection when inference is installed.

    Every process that changes the library binds the projection, so each
    change records what search must re-project; the ``search.*`` Jobs do the
    projecting and indexing. Returns what was bound before, for
    ``restore_search``.
    """
    from app.bootstrap.optional_features import inference_available
    from app.db.content_search import bind_content_search
    from app.db.projections import bind_content_projection

    previous = (bind_content_search(None), bind_content_projection(None))
    if inference_available():
        from app.modules.search.lexical_query import LibrarySearch
        from app.modules.search.projection import LibraryProjection

        bind_content_search(LibrarySearch())
        bind_content_projection(LibraryProjection())
    return previous


def start_model_warmup():
    """Warm the API's query models when local inference is installed."""
    from app.bootstrap.optional_features import inference_available

    if not inference_available():
        return None
    from app.modules.search.model_warmup import WarmupSupervisor

    supervisor = WarmupSupervisor(get_session_factory())
    supervisor.start()
    return supervisor


def close_inference() -> None:
    """Stop this process's query and model workers and its inference client.

    Blocking; every process that ran inference (the API, a worker) calls it
    on shutdown. Model downloads are Jobs, which the engine's shutdown ends.
    """
    from app.bootstrap.optional_features import inference_available

    if not inference_available():
        return
    from app.modules.inference.query import close_queries
    from app.modules.inference.transport import close_client
    from app.modules.inference.worker_pool import pool as model_workers

    close_queries()
    model_workers.close()
    close_client()


def restore_search(previous: SearchBindings) -> None:
    from app.db.content_search import bind_content_search
    from app.db.projections import bind_content_projection

    search, projection = previous
    bind_content_search(search)  # type: ignore[arg-type]
    bind_content_projection(projection)  # type: ignore[arg-type]


def _start_work(prepared: PreparedProcess, *, publisher) -> None:
    """Start background work unless an interrupted restore still governs.

    Held work starts when recovery resolves that restore (``release_held``).
    """
    from app.bootstrap.work import hold, start

    # Only the lifespan runs this, after it took the vault's API lock.
    if prepared.restore_maintenance:
        logger.warning("background work held: restore maintenance is active")
        hold(publisher=publisher, sole_api=True)
        return
    start(publisher=publisher, sole_api=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.modules.storage.storage_paths import validate_runtime_storage_paths

    if settings.process_role is ProcessRole.WORKER:
        raise RuntimeError(
            "VAULT_PROCESS_ROLE=worker runs `python -m app.worker`, not the HTTP app"
        )
    # Validate environment-time paths before even creating the process-lock
    # rendezvous beside the database.
    validate_runtime_storage_paths()
    process_lock = acquire_process_lock()
    app.state.process_lock = process_lock
    logger.info("starting %s v%s", settings.app_name, settings.app_version)
    _app_info.info({"version": settings.app_version, "name": settings.app_name})
    prepared = prepare_process(owner=True)
    previous_search = bind_search()
    printer_provider_registry = build_provider_registry()
    app.state.printer_provider_registry = printer_provider_registry
    provider_builder = partial(get_provider_client, registry=printer_provider_registry)
    from app.modules.printing.jobs import bind_provider_builder
    from app.runtime.realtime import build_event_bus

    bind_provider_builder(provider_builder)
    bus = build_event_bus()
    app.state.event_bus = bus
    await bus.start()
    hub = PrinterHub(
        bus,
        session_factory=get_session_factory(),
        provider_builder=provider_builder,
    )
    app.state.printer_hub = hub
    watcher = LibraryWatcher()
    app.state.library_watcher = watcher
    # Off the event loop: the engine refuses to register its queues from a
    # running loop, and the startup reconcile is blocking database work.
    await asyncio.to_thread(_start_work, prepared, publisher=bus)
    warmup = start_model_warmup()
    await hub.start_all()
    # Real-time folder watching is best-effort: never let it block startup.
    try:
        await watcher.start_all()
    except Exception:
        logger.exception("library watcher failed to start; scheduled scans still run")
    yield
    from app.bootstrap.work import stop as stop_work
    from app.modules.storage.materializer_runtime import bind_materializer

    restore_search(previous_search)
    bind_materializer(None)
    logger.info("shutting down printer hub")
    await watcher.stop_all()
    await hub.stop_all()
    if warmup is not None:
        await asyncio.to_thread(warmup.stop)
    await asyncio.to_thread(stop_work)
    await bus.stop()
    try:
        await _close_outbound_clients()
    finally:
        release_process_lock(process_lock)
    logger.info("shutting down")
