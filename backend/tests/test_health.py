"""Coverage for the operational health probes (app/api/v1/health.py).

Complements test_api_hardening.py (auth/shape) and test_r2_ops.py (jobs /
external-libraries happy paths) by exercising each probe's error branch and
the Spoolman probe, which neither of those files touch at all.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

import app.api.v1.health as health_mod
from app.db.models import Printer, PrinterStatus

# --------------------------------------------------------------------------- #
# _database_probe
# --------------------------------------------------------------------------- #


def test_database_probe_ok(db_session: Session) -> None:
    out = health_mod._database_probe()
    assert out["ok"] is True
    assert "counts" in out


def test_database_probe_reports_error_on_failure(monkeypatch) -> None:
    class _BoomFactory:
        def session(self):
            raise RuntimeError("db unreachable")

    monkeypatch.setattr(health_mod, "get_session_factory", lambda: _BoomFactory())
    out = health_mod._database_probe()
    assert out["ok"] is False
    assert out["error"] == "RuntimeError"


# --------------------------------------------------------------------------- #
# _backup_probe
# --------------------------------------------------------------------------- #


def test_backup_probe_ok(tmp_path) -> None:
    from app.core.config import _overlay

    _overlay["backup_dir"] = tmp_path / "backups"
    (tmp_path / "backups").mkdir()
    out = health_mod._backup_probe()
    assert out["ok"] is True
    assert out["local_count"] == 0
    assert out["latest"] is None


def test_backup_probe_reports_oserror(monkeypatch, tmp_path) -> None:
    """A real ``pathlib.Path.glob()`` silently swallows PermissionError, so the
    OSError branch is forced via a fake ``Path`` bound only in the health
    module's namespace — the real ``pathlib.Path`` used everywhere else in
    the process is untouched."""
    from app.core.config import _overlay
    from app.core.config import settings as real_settings

    _overlay["backup_dir"] = tmp_path / "backups"
    real_path = health_mod.Path

    class _BoomPath:
        def __init__(self, *a, **k):
            pass

        def glob(self, *_a, **_k):
            raise OSError("disk error")

        def exists(self):
            return True

        def is_dir(self):
            return True

        def __str__(self):
            return str(real_settings.backup_dir)

    monkeypatch.setattr(health_mod, "Path", _BoomPath)
    try:
        out = health_mod._backup_probe()
    finally:
        monkeypatch.setattr(health_mod, "Path", real_path)
    assert out["ok"] is False
    assert out["error"] == "OSError"


# --------------------------------------------------------------------------- #
# _storage_probe
# --------------------------------------------------------------------------- #


def test_storage_probe_reports_error_on_failure(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(health_mod, "get_backend", _boom)
    out = health_mod._storage_probe()
    assert out["ok"] is False
    assert out["error"] == "RuntimeError"


# --------------------------------------------------------------------------- #
# _provider_probe
# --------------------------------------------------------------------------- #


def test_provider_probe_counts_live_printers(db_session: Session) -> None:
    db_session.add(
        Printer(name="Ender", moonraker_url="http://x", status=PrinterStatus.READY)
    )
    db_session.commit()

    out = health_mod._provider_probe()
    assert out["ok"] is True
    assert out["configured"]["moonraker"] == 1
    assert out["status_counts"]["ready"] == 1
    assert len(out["providers"]) == 5


def test_provider_probe_reports_error_on_failure(monkeypatch) -> None:
    class _BoomFactory:
        def session(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(health_mod, "get_session_factory", lambda: _BoomFactory())
    out = health_mod._provider_probe()
    assert out["ok"] is False
    assert out["error"] == "RuntimeError"
    assert "providers" in out  # capability summary still surfaced


# --------------------------------------------------------------------------- #
# _jobs_probe
# --------------------------------------------------------------------------- #


def test_jobs_probe_reports_error_on_failure(monkeypatch) -> None:
    import app.services.jobs as jobs_mod

    def _boom():
        raise RuntimeError("registry broken")

    monkeypatch.setattr(jobs_mod.registry, "snapshot_counts", _boom)
    out = health_mod._jobs_probe()
    assert out["ok"] is False
    assert out["error"] == "RuntimeError"


# --------------------------------------------------------------------------- #
# _fleet_scheduler_probe
# --------------------------------------------------------------------------- #


def test_fleet_scheduler_probe_reports_error_on_failure(monkeypatch) -> None:
    class _BoomFactory:
        def session(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(health_mod, "get_session_factory", lambda: _BoomFactory())
    out = health_mod._fleet_scheduler_probe()
    assert out["ok"] is False
    assert out["error"] == "RuntimeError"


# --------------------------------------------------------------------------- #
# _external_libraries_probe
# --------------------------------------------------------------------------- #


def test_external_libraries_probe_reports_error_on_failure(monkeypatch) -> None:
    class _BoomFactory:
        def session(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(health_mod, "get_session_factory", lambda: _BoomFactory())
    out = health_mod._external_libraries_probe()
    assert out["ok"] is False
    assert out["error"] == "RuntimeError"


# --------------------------------------------------------------------------- #
# _spoolman_probe — entirely untested elsewhere
# --------------------------------------------------------------------------- #


def test_spoolman_probe_disabled_by_default(db_session: Session) -> None:
    out = health_mod._spoolman_probe()
    assert out == {"ok": True, "enabled": False}


def test_spoolman_probe_enabled_but_unconfigured(db_session: Session) -> None:
    from app.services import runtime_config

    runtime_config.set_spoolman_enabled(db_session, True)
    out = health_mod._spoolman_probe()
    assert out == {"ok": True, "enabled": True, "configured": False}


def test_spoolman_probe_reachable(db_session: Session, monkeypatch) -> None:
    from app.services import runtime_config

    runtime_config.set_spoolman_enabled(db_session, True)
    runtime_config.set_spoolman_config(
        db_session, base_url="http://spoolman.local:7912", api_key="secret"
    )

    class _Resp:
        status_code = 200

        def json(self):
            return {"version": "1.9.0"}

    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp())
    out = health_mod._spoolman_probe()
    assert out["ok"] is True
    assert out["reachable"] is True
    assert out["version"] == "1.9.0"


def test_spoolman_probe_unreachable_http_error(
    db_session: Session, monkeypatch
) -> None:
    import httpx

    from app.services import runtime_config

    runtime_config.set_spoolman_enabled(db_session, True)
    runtime_config.set_spoolman_config(
        db_session, base_url="http://spoolman.local:7912"
    )

    def _boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("httpx.get", _boom)
    out = health_mod._spoolman_probe()
    assert out["ok"] is True
    assert out["reachable"] is False
    assert out["error"] == "ConnectError"


def test_spoolman_probe_reports_error_on_failure(monkeypatch) -> None:
    class _BoomFactory:
        def session(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(health_mod, "get_session_factory", lambda: _BoomFactory())
    out = health_mod._spoolman_probe()
    assert out["ok"] is False
    assert out["error"] == "RuntimeError"


# --------------------------------------------------------------------------- #
# Full endpoint: a degraded component flips overall status
# --------------------------------------------------------------------------- #


def test_health_details_flips_to_degraded_when_a_component_fails(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    def _boom():
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(health_mod, "get_backend", _boom)
    body = client.get("/api/v1/health/details", headers=auth_headers).json()
    assert body["status"] == "degraded"
    assert body["components"]["storage"]["ok"] is False
