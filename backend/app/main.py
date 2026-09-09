from __future__ import annotations

import hmac
import time
import uuid
from datetime import datetime
from enum import Enum

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlmodel import col, select
from starlette import status

from app.api.errors import operation_error_response
from app.api.session_cookie import extract_access_token
from app.api.v1 import api_router
from app.api.vault_generation import VaultGenerationMiddleware
from app.bootstrap.lifecycle import (
    lifespan,
)
from app.core.body_limit import RequestBodyLimitMiddleware
from app.core.config import settings
from app.core.errors import OperationError
from app.core.logging import get_logger
from app.core.metrics import (
    background_job_depth,
    fleet_blocked_jobs,
    fleet_jobs,
    fleet_scheduler_last_tick,
    fleet_scheduler_running,
    observe_request,
    printer_status,
    staging_bytes,
    storage_delete_intents,
)
from app.core.metrics import registry as _metrics_registry
from app.db.session import get_session_factory
from app.modules.administration.audit import (
    clear_audit_context,
    set_audit_context,
)

logger = get_logger(__name__)


def _metric_enum_value(value: object) -> str:
    """Render SQL enum values consistently for Prometheus labels."""
    return str(value.value) if isinstance(value, Enum) else str(value)


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


app.add_exception_handler(OperationError, operation_error_response)


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
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(VaultGenerationMiddleware)


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
    from app.modules.identity.auth import (  # deferred: avoids cycle
        verify_access_token,
    )

    token = extract_access_token(request)
    if token:
        payload = verify_access_token(token)
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
            provider=_metric_enum_value(provider),
            status=_metric_enum_value(prn_status),
        ).inc()


def _refresh_fleet_gauges() -> None:
    from sqlalchemy import func

    from app.db.models import PrintJob
    from app.modules.printing.printer_jobs import scheduler_snapshot

    try:
        with get_session_factory().session() as session:
            rows = session.exec(
                select(col(PrintJob.state), func.count(col(PrintJob.id))).group_by(
                    col(PrintJob.state)
                )
            ).all()
            blocked = session.exec(
                select(func.count(col(PrintJob.id))).where(
                    PrintJob.blocked_reason.is_not(None)  # type: ignore[union-attr]
                )
            ).one()
    except Exception:
        logger.exception("metrics: failed to refresh fleet gauges")
        return
    fleet_jobs.clear()
    for state, count in rows:
        fleet_jobs.labels(state=_metric_enum_value(state)).set(count)
    fleet_blocked_jobs.set(blocked)
    snapshot = scheduler_snapshot()
    fleet_scheduler_running.set(1 if snapshot["running"] else 0)
    last_tick = snapshot["last_tick_at"]
    fleet_scheduler_last_tick.set(
        last_tick.timestamp() if isinstance(last_tick, datetime) else 0
    )


def _refresh_persistence_gauges() -> None:
    from sqlalchemy import func

    from app.db.models import BackgroundJob, StagingLease, StorageDeleteIntent

    try:
        with get_session_factory().session() as session:
            jobs = session.exec(
                select(
                    col(BackgroundJob.state), func.count(col(BackgroundJob.id))
                ).group_by(col(BackgroundJob.state))
            ).all()
            staged = session.exec(
                select(func.coalesce(func.sum(StagingLease.size_bytes), 0))
            ).one()
            intents = session.exec(
                select(
                    StorageDeleteIntent.status,
                    func.count(col(StorageDeleteIntent.id)),
                ).group_by(StorageDeleteIntent.status)
            ).all()
    except Exception:
        logger.exception("metrics: failed to refresh persistence gauges")
        return
    background_job_depth.clear()
    for state, count in jobs:
        background_job_depth.labels(state=str(state)).set(count)
    staging_bytes.set(int(staged))
    storage_delete_intents.clear()
    for intent_state, count in intents:
        storage_delete_intents.labels(state=intent_state).set(count)


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
            return Response(
                status_code=status.HTTP_401_UNAUTHORIZED, content="unauthorized"
            )
    _refresh_printer_gauge()
    _refresh_fleet_gauges()
    _refresh_persistence_gauges()
    return Response(
        content=generate_latest(_metrics_registry), media_type=CONTENT_TYPE_LATEST
    )
