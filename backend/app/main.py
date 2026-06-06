from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import time
import uuid

from fastapi import FastAPI
from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.engine.url import make_url
from starlette import status

from app.api.v1 import api_router
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_session_factory, init_db
from app.services.audit import (
    clear_audit_context,
    install_audit_listeners,
    set_audit_context,
)
from app.services.lifecycle import gc_soft_deleted
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
    # DB must exist before we can read the runtime overlay.
    init_db()
    with get_session_factory().scoped_session() as session:
        apply_overlay(session)
        configured = is_configured(session)
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
    app.state.gc_task = asyncio.create_task(_gc_loop())
    await hub.start_all()
    yield
    logger.info("shutting down printer hub")
    app.state.gc_task.cancel()
    await hub.stop_all()
    logger.info("shutting down")


async def _gc_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            gc_soft_deleted()
        except Exception:
            logger.exception("scheduled GC failed")


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
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
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
        from app.services.auth import verify_access_token

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
