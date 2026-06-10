from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import func, text
from sqlalchemy.engine.url import make_url
from sqlmodel import select

from app.core.config import settings
from app.db.models import File, Model, PrintJob, Printer, PrinterProvider
from app.db.session import get_session_factory
from app.services.printer_provider import provider_diagnostic_summary
from app.services.storage_backend import get_backend
from app.db.scopes import live

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
    }
    out["components"] = components
    out["metrics"] = components["database"].get("counts", {})
    for component in components.values():
        if not component.get("ok", False):
            _mark_degraded(out)
    return out
