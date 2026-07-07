from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import hmac
import time
import uuid

from fastapi import FastAPI
from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.engine.url import make_url
from sqlmodel import select
from starlette import status

from app.core.metrics import observe_request, printer_status
from app.core.metrics import app_info as _app_info
from app.core.metrics import registry as _metrics_registry

from app.api.v1 import api_router
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_session_factory, init_db
from app.services.audit import (
    clear_audit_context,
    install_audit_listeners,
    set_audit_context,
)
from app.services.trash import gc_soft_deleted
from app.services.library_watcher import LibraryWatcher
from app.services.notifications import run_dispatcher_loop
from app.services.printer_hub import PrinterHub
from app.services.runtime_config import apply_overlay, is_configured
from app.services.storage_backend import init_backend

logger = get_logger(__name__)


def _safe_db_url(value: str) -> str:
    try:
        return make_url(value).render_as_string(hide_password=True)
    except Exception:
        return "<invalid-db-url>"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting %s v%s", settings.app_name, settings.app_version)
    _app_info.info({"version": settings.app_version, "name": settings.app_name})
    # DB must exist before we can read the runtime overlay.
    init_db()
    with get_session_factory().scoped_session() as session:
        apply_overlay(session)
        configured = is_configured(session)
        # Clear any NAS scans stranded RUNNING by a previous unclean shutdown,
        # otherwise the scheduler would skip them forever.
        from app.services.external_library import reset_orphaned_scans

        reset_count = reset_orphaned_scans(session)
        if reset_count:
            logger.warning(
                "reset %d external library scan(s) stranded by restart", reset_count
            )
    # Initialise storage backend (creates dirs or validates S3 bucket).
    _backend = init_backend()
    if not configured:
        logger.warning(
            "vault is unconfigured — open the web UI to run the first-run setup wizard"
        )
    logger.info(
        "backend=%s data_dir=%s thumb_dir=%s db=%s",
        settings.storage_backend,
        settings.data_dir,
        settings.thumb_dir,
        _safe_db_url(settings.db_url),
    )
    install_audit_listeners()
    hub = PrinterHub()
    app.state.printer_hub = hub
    watcher = LibraryWatcher()
    app.state.library_watcher = watcher
    app.state.gc_task = asyncio.create_task(_gc_loop())
    app.state.external_scan_task = asyncio.create_task(_external_scan_loop())
    app.state.notification_task = asyncio.create_task(run_dispatcher_loop())
    await hub.start_all()
    # Real-time folder watching is best-effort: never let it block startup.
    try:
        await watcher.start_all()
    except Exception:
        logger.exception("library watcher failed to start; scheduled scans still run")
    yield
    logger.info("shutting down printer hub")
    app.state.gc_task.cancel()
    app.state.external_scan_task.cancel()
    app.state.notification_task.cancel()
    await watcher.stop_all()
    await hub.stop_all()
    from app.services.browser_fetch import close_browser
    from app.services.moonraker import close_http_client

    await close_http_client()
    await close_browser()
    logger.info("shutting down")


async def _gc_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            # Sync DB + storage I/O — keep it off the event loop.
            await asyncio.to_thread(gc_soft_deleted)
        except Exception:
            logger.exception("scheduled GC failed")
        try:
            from app.services.notifications import prune_deliveries

            await asyncio.to_thread(prune_deliveries)
        except Exception:
            logger.exception("notification delivery pruning failed")


async def _external_scan_loop() -> None:
    """Poll enabled external (NAS) libraries and scan those whose interval elapsed.

    No-op while the ``external_libraries_enabled`` opt-in is off. All blocking DB
    and filesystem work runs in a worker thread to keep the event loop free.
    """
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(_run_due_external_scans)
        except Exception:
            logger.exception("external library scan tick failed")


def _run_due_external_scans() -> None:
    from app.services import external_library
    from app.services.runtime_config import external_libraries_enabled

    with get_session_factory().scoped_session() as session:
        if not external_libraries_enabled(session):
            return
        due = external_library.libraries_due_for_scan(session)
    for library_id in due:
        try:
            external_library.scan_library(library_id)
        except Exception:
            logger.exception("scheduled scan failed for library %s", library_id)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Self-hosted, Plex-style asset management for 3D printing workflows. "
        "Stages 1–3: headless API, OrcaSlicer ingestion, categories/tags, and "
        "Klipper/Moonraker integration with live state + print history."
    ),
    lifespan=lifespan,
)


def _parse_cors_origins(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


_cors_origins = _parse_cors_origins(settings.cors_origins) or [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
_allow_all_cors = "*" in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=not _allow_all_cors,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            {"detail": "request_validation_failed", "errors": exc.errors()}
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if str(settings.log_level).upper() == "DEBUG":
        logger.exception(
            "unhandled request error method=%s path=%s request_id=%s",
            request.method,
            request.url.path,
            getattr(request.state, "request_id", "-"),
        )
    else:
        logger.error(
            "unhandled request error method=%s path=%s request_id=%s error=%s",
            request.method,
            request.url.path,
            getattr(request.state, "request_id", "-"),
            exc.__class__.__name__,
        )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "internal_server_error"},
    )


@app.middleware("http")
async def bind_audit_context(request: Request, call_next):
    actor_id = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        from app.services.auth import verify_access_token  # deferred: avoids cycle

        payload = verify_access_token(auth.split(" ", 1)[1])
        if payload and payload.get("sub"):
            try:
                actor_id = int(payload["sub"])
            except (TypeError, ValueError):
                actor_id = None
    set_audit_context(
        actor_id=actor_id, ip=request.client.host if request.client else None
    )
    try:
        return await call_next(request)
    finally:
        clear_audit_context()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        # Record request latency, keyed by the matched route template (not the
        # raw path) to bound label cardinality. Skip /metrics to avoid self-noise.
        if request.url.path != "/metrics":
            route = request.scope.get("route")
            path_label = getattr(route, "path", None) or "unmatched"
            observe_request(
                request.method, path_label, status_code, duration_ms / 1000.0
            )
        if request.url.path == "/api/v1/health" and status_code < 500:
            log_fn = logger.debug
        elif status_code >= 500:
            log_fn = logger.error
        elif status_code >= 400:
            log_fn = logger.warning
        else:
            log_fn = logger.info
        log_fn(
            "request method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
            request_id,
        )


app.include_router(api_router)


def _refresh_printer_gauge() -> None:
    """Repopulate the printer-status gauge from current DB state (pull-style).

    Cleared and rebuilt each scrape so printers removed since the last scrape do
    not linger as stale series.
    """
    from app.db.models import Printer
    from app.db.scopes import live

    try:
        with get_session_factory().session() as session:
            rows = session.exec(
                select(Printer.provider, Printer.status).where(live(Printer))
            ).all()
    except Exception:
        logger.exception("metrics: failed to refresh printer gauge")
        return
    printer_status.clear()
    for provider, prn_status in rows:
        printer_status.labels(
            provider=provider.value, status=prn_status.value
        ).inc()


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint(request: Request) -> Response:
    """Prometheus exposition endpoint.

    Open by default; set ``VAULT_METRICS_TOKEN`` to require a static bearer
    token. Defined as a sync handler so the DB query + render run off the event
    loop in FastAPI's threadpool.
    """
    token = settings.metrics_token
    if token:
        auth = request.headers.get("authorization", "")
        provided = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(provided, token):
            return Response(status_code=status.HTTP_401_UNAUTHORIZED, content="unauthorized")
    _refresh_printer_gauge()
    return Response(content=generate_latest(_metrics_registry), media_type=CONTENT_TYPE_LATEST)
