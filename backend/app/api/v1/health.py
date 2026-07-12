from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import func, text
from sqlalchemy.engine.url import make_url
from sqlmodel import select

from app.core.config import settings
from app.db.models import (
    ExternalLibrary,
    ExternalLibraryScanStatus,
    File,
    Model,
    Printer,
    PrinterProvider,
    PrintJob,
)
from app.db.scopes import live
from app.db.session import get_session_factory
from app.services.printer_provider import provider_diagnostic_summary
from app.services.storage_backend import get_backend

router = APIRouter(tags=["health"])


def _mark_degraded(out: dict) -> None:
    out["status"] = "degraded"


def _database_probe() -> dict:
    try:
        url = make_url(settings.db_url)
        with get_session_factory().session() as session:
            session.exec(text("SELECT 1"))
            counts = {
                "models": session.exec(select(func.count(Model.id))).one(),
                "files": session.exec(select(func.count(File.id))).one(),
                "printers": session.exec(select(func.count(Printer.id))).one(),
                "print_jobs": session.exec(select(func.count(PrintJob.id))).one(),
            }
        return {
            "ok": True,
            "backend": url.get_backend_name(),
            "database": url.database or "",
            "counts": counts,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": exc.__class__.__name__,
        }


def _backup_probe() -> dict:
    backup_dir = Path(settings.backup_dir)
    try:
        backups = sorted(
            [
                *backup_dir.glob("printstash-backup-*.tar.gz"),
                *backup_dir.glob("nexus3d-backup-*.tar.gz"),
            ]
        )
        return {
            "ok": backup_dir.exists() and backup_dir.is_dir(),
            "path": str(backup_dir),
            "local_count": len(backups),
            "latest": backups[-1].name if backups else None,
            "s3_configured": bool(settings.backup_s3_bucket),
        }
    except OSError as exc:
        return {
            "ok": False,
            "path": str(backup_dir),
            "error": exc.__class__.__name__,
            "s3_configured": bool(settings.backup_s3_bucket),
        }


def _storage_probe() -> dict:
    try:
        return get_backend().health_probe()
    except Exception as exc:
        return {
            "ok": False,
            "backend": settings.storage_backend,
            "error": exc.__class__.__name__,
        }


def _provider_probe() -> dict:
    providers = [
        provider_diagnostic_summary(PrinterProvider.MOONRAKER),
        provider_diagnostic_summary(PrinterProvider.BAMBU_LAN),
        provider_diagnostic_summary(PrinterProvider.PRUSALINK),
        provider_diagnostic_summary(PrinterProvider.ELEGOO_CENTAURI),
        provider_diagnostic_summary(PrinterProvider.OCTOPRINT),
    ]
    try:
        with get_session_factory().session() as session:
            rows = session.exec(
                select(Printer.provider, Printer.status).where(live(Printer))
            ).all()
        provider_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        for provider, status in rows:
            provider_counts[provider.value] = provider_counts.get(provider.value, 0) + 1
            status_counts[status.value] = status_counts.get(status.value, 0) + 1
        return {
            "ok": True,
            "configured": provider_counts,
            "status_counts": status_counts,
            "providers": providers,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": exc.__class__.__name__,
            "providers": providers,
        }


def _jobs_probe() -> dict:
    # In-memory ingestion registry; informational, so always ``ok``.
    from app.services.jobs import registry

    try:
        return {"ok": True, "counts": registry.snapshot_counts()}
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}


def _external_libraries_probe() -> dict:
    # Informational: RUNNING usually means a scan is genuinely in progress
    # (orphans are reset at startup), so this never flips the overall status.
    try:
        with get_session_factory().session() as session:
            rows = session.exec(
                select(ExternalLibrary.enabled, ExternalLibrary.last_scan_status)
            ).all()
        enabled = 0
        status_counts: dict[str, int] = {}
        for is_enabled, status in rows:
            if is_enabled:
                enabled += 1
            if status is not None:
                status_counts[status.value] = status_counts.get(status.value, 0) + 1
        return {
            "ok": True,
            "configured": len(rows),
            "enabled": enabled,
            "status_counts": status_counts,
            "running": status_counts.get(ExternalLibraryScanStatus.RUNNING.value, 0),
        }
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}


def _spoolman_probe() -> dict:
    # Informational, mirroring _external_libraries_probe: while disabled (or
    # unreachable) it returns ok=True so an optional, OFF-by-default integration
    # never flips the overall service status to degraded.
    try:
        from app.services import runtime_config

        with get_session_factory().session() as session:
            if not runtime_config.spoolman_enabled(session):
                return {"ok": True, "enabled": False}
            config = runtime_config.spoolman_config(session)
        base_url = config.get("base_url")
        if not base_url:
            return {"ok": True, "enabled": True, "configured": False}

        import httpx

        headers = {}
        if config.get("api_key"):
            headers["Authorization"] = f"Bearer {config['api_key']}"
            headers["X-Api-Key"] = config["api_key"]
        try:
            resp = httpx.get(
                f"{base_url.rstrip('/')}/api/v1/info", headers=headers, timeout=5.0
            )
            reachable = 200 <= resp.status_code < 300
            version = resp.json().get("version") if reachable else None
        except httpx.HTTPError as exc:
            return {
                "ok": True,
                "enabled": True,
                "configured": True,
                "reachable": False,
                "error": exc.__class__.__name__,
            }
        return {
            "ok": True,
            "enabled": True,
            "configured": True,
            "reachable": reachable,
            "version": version,
        }
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}


@router.get(
    "/health",
    summary="Operational health probe",
    description=(
        "Returns service identity, database/storage/backup readiness, and "
        "provider capability diagnostics. Used by Docker healthcheck and "
        "self-hosted release verification."
    ),
)
def health() -> dict:
    out = {
        "status": "ok",
        "name": settings.app_name,
        "version": settings.app_version,
    }
    components = {
        "database": _database_probe(),
        "storage": _storage_probe(),
        "backup": _backup_probe(),
        "printer_providers": _provider_probe(),
        "jobs": _jobs_probe(),
        "external_libraries": _external_libraries_probe(),
        "spoolman": _spoolman_probe(),
    }
    out["components"] = components
    out["metrics"] = components["database"].get("counts", {})
    for component in components.values():
        if not component.get("ok", False):
            _mark_degraded(out)
    return out
