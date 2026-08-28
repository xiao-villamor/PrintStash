"""What ``/health``, ``/health/details`` and ``/health/releases/latest`` promise.

The detailed probe is the operator's diagnosis: the Docker healthcheck and the release
verification in ``docs/release-validation.md`` both read it, and the UI disables printer
controls from the capability summary it returns. So its shape is a contract — every
component key, the provider support levels, and above all *when* the service calls
itself degraded. A broken dependency (database, storage, backup) must degrade it; an
optional integration that is merely switched off or unreachable must not, or every
self-hoster without Spoolman sees a red service.

The public ``/health`` is the other half of that contract: it answers an unauthenticated
caller, so it must disclose liveness and nothing else.

Probe error branches — the ones a real dependency cannot produce on demand — live in
``unit/api/v1/test_health.py``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app.api.v1.health as health_mod
from app.core.config import get_config
from app.core.time import utcnow
from app.db.models import (
    ExternalLibrary,
    ExternalLibraryScanStatus,
    File,
    FileType,
    Model,
    Printer,
    PrinterProvider,
    PrinterStatus,
    PrintJobState,
)
from tests.factories import (
    build_file,
    build_model,
    build_print_job,
    printer_config,
)
from tests.integration.conftest import UserHeaders

SPOOLMAN_URL = "http://spoolman.local:7912"
SPOOLMAN_KEY = "spoolman-api-key"


def _printer(name: str = "Ender", **overrides: Any) -> Printer:
    fields: dict[str, Any] = {
        "name": name,
        "moonraker_url": "http://printer.local:7125",
        "status": PrinterStatus.READY,
    }
    fields.update(overrides)
    return printer_config(**fields)


def _model_with_file(db_session: Session, name: str) -> Model:
    model = build_model(
        db_session, name=name, slug=name.lower(), hash=f"{name.lower():x<64}"[:64]
    )
    build_file(
        db_session,
        model,
        path=f"{name}.gcode",
        filename=f"{name}.gcode",
        file_type=FileType.GCODE,
        size_bytes=10,
        sha256=f"{name.lower():f<64}"[:64],
    )
    return model


@pytest.fixture
def spoolman_enabled(db_session: Session):
    """Turn the optional Spoolman integration on, with an optional API key."""
    from app.services import runtime_config

    def enable(*, base_url: str | None = SPOOLMAN_URL, api_key: str | None = None):
        runtime_config.set_spoolman_enabled(db_session, True)
        if base_url is not None:
            runtime_config.set_spoolman_config(
                db_session, base_url=base_url, api_key=api_key
            )

    return enable


@pytest.fixture
def spoolman_server(monkeypatch: pytest.MonkeyPatch):
    """Stand in for the Spoolman server, recording the request it received."""
    calls: list[dict[str, Any]] = []

    def respond(
        *,
        status_code: int = 200,
        body: dict | None = None,
        error: Exception | None = None,
    ):
        def fake_get(url: str, **kwargs: Any):
            calls.append({"url": url, "headers": kwargs.get("headers", {})})
            if error is not None:
                raise error
            return httpx.Response(
                status_code, json=body if body is not None else {"version": "1.9.0"}
            )

        monkeypatch.setattr(httpx, "get", fake_get)
        return calls

    return respond


class TestHealth:
    def test_reports_ok_with_the_configured_app_name(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ok"
        assert body["name"] == get_config().app_name
        assert body["storage"]["tier"] == "verified"

    def test_needs_no_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").status_code == 200

    def test_discloses_only_liveness_and_safe_storage_metadata(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/v1/health").json()

        assert set(body) == {"status", "name", "storage"}
        assert set(body["storage"]) == {
            "provider",
            "capabilities",
            "tier",
            "diagnostics",
            "warnings",
        }


class TestHealthDetails:
    def test_reports_the_running_version(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = client.get("/api/v1/health/details", headers=auth_headers).json()

        assert body["version"] == get_config().app_version

    def test_reports_the_deployment_shape(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = client.get("/api/v1/health/details", headers=auth_headers).json()

        assert body["deployment"] == {
            "mode": "single_process",
            "processes": 1,
            "distributed_coordination": False,
        }

    def test_reports_every_component(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = client.get("/api/v1/health/details", headers=auth_headers).json()

        assert set(body["components"]) == {
            "database",
            "storage",
            "backup",
            "printer_providers",
            "jobs",
            "fleet_scheduler",
            "external_libraries",
            "spoolman",
        }

    def test_mirrors_database_counts_into_metrics(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        _model_with_file(db_session, "Widget")

        body = client.get("/api/v1/health/details", headers=auth_headers).json()

        assert body["metrics"] == body["components"]["database"]["counts"]

    def test_reports_provider_support_levels(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = client.get("/api/v1/health/details", headers=auth_headers).json()

        summaries = {
            p["provider"]: p
            for p in body["components"]["printer_providers"]["providers"]
        }
        assert set(summaries) == {p.value for p in PrinterProvider}
        for provider, summary in summaries.items():
            assert summary["support_level"], f"{provider} has no support level"
            assert "unsupported_actions" in summary, provider

    def test_flips_to_degraded_when_a_component_fails(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def unavailable():
            raise RuntimeError("backend unavailable")

        monkeypatch.setattr(health_mod, "get_backend", unavailable)

        body = client.get("/api/v1/health/details", headers=auth_headers).json()

        assert body["status"] == "degraded"
        assert body["components"]["storage"]["ok"] is False

    def test_stays_ok_when_an_informational_component_is_unreachable(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        spoolman_enabled,
        spoolman_server,
    ) -> None:
        spoolman_enabled()
        spoolman_server(error=httpx.ConnectError("connection refused"))

        body = client.get("/api/v1/health/details", headers=auth_headers).json()

        assert body["status"] == "ok", (
            "an optional integration must not degrade the service"
        )
        assert body["components"]["spoolman"]["reachable"] is False

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/health/details").status_code == 401

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("operator")

        response = client.get("/api/v1/health/details", headers=headers)

        assert response.status_code == 403, response.text


class TestLatestRelease:
    @pytest.fixture
    def release_check(self, monkeypatch: pytest.MonkeyPatch):
        """Stand in for the GitHub release lookup, recording how it was called."""
        calls: list[dict[str, Any]] = []

        async def fake_release_status(current_version: str, *, force: bool = False):
            calls.append({"current_version": current_version, "force": force})
            return {
                "status": "up_to_date",
                "current_version": current_version,
                "latest_version": current_version,
                "update_available": False,
                "release_url": "https://example.test/release",
                "published_at": "2026-07-14T10:00:00Z",
                "checked_at": "2026-07-14T11:00:00Z",
            }

        monkeypatch.setattr(health_mod, "get_release_status", fake_release_status)
        return calls

    def test_returns_the_release_status(
        self, client: TestClient, auth_headers: dict[str, str], release_check
    ) -> None:
        response = client.get("/api/v1/health/releases/latest", headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "up_to_date"
        assert body["current_version"] == get_config().app_version
        assert body["update_available"] is False

    def test_forces_a_recheck_when_refresh_is_set(
        self, client: TestClient, auth_headers: dict[str, str], release_check
    ) -> None:
        client.get("/api/v1/health/releases/latest?refresh=true", headers=auth_headers)

        assert [call["force"] for call in release_check] == [True]

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/health/releases/latest").status_code == 401

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("release-reader")

        response = client.get("/api/v1/health/releases/latest", headers=headers)

        assert response.status_code == 403, response.text


class TestDatabaseProbe:
    def test_reports_the_database_backend_with_its_row_counts(
        self, db_session: Session
    ) -> None:
        _model_with_file(db_session, "Widget")

        out = health_mod._database_probe()

        assert out["ok"] is True
        assert out["backend"] == "sqlite"
        # The sentinel rows the suite seeds are real rows; count the delta instead.
        assert out["counts"]["models"] >= 1
        assert out["counts"]["files"] >= 1


class TestProviderProbe:
    def test_counts_live_printers_per_provider_per_status(
        self, db_session: Session
    ) -> None:
        db_session.add(_printer("Ender"))
        db_session.commit()

        out = health_mod._provider_probe()

        assert out["ok"] is True
        assert out["configured"][PrinterProvider.MOONRAKER.value] == 1
        assert out["status_counts"][PrinterStatus.READY.value] == 1

    def test_ignores_a_trashed_printer(self, db_session: Session) -> None:
        db_session.add(_printer("Trashed", deleted_at=utcnow()))
        db_session.commit()

        out = health_mod._provider_probe()

        assert out["configured"] == {}
        assert out["status_counts"] == {}


class TestFleetSchedulerProbe:
    def test_counts_print_jobs_per_state(self, db_session: Session) -> None:
        printer = _printer("Queue Owner")
        db_session.add(printer)
        model = _model_with_file(db_session, "Queued")
        db_session.commit()
        db_session.refresh(printer)
        gcode = db_session.exec(select(File).where(File.model_id == model.id)).one()
        build_print_job(db_session, gcode, printer=printer)

        out = health_mod._fleet_scheduler_probe()

        assert out["counts"][PrintJobState.QUEUED.value] == 1

    def test_zero_fills_every_state(self, db_session: Session) -> None:
        out = health_mod._fleet_scheduler_probe()

        assert set(out["counts"]) == {state.value for state in PrintJobState}
        assert set(out["counts"].values()) == {0}

    def test_surfaces_the_scheduler_snapshot(self, db_session: Session) -> None:
        out = health_mod._fleet_scheduler_probe()

        assert set(out) >= {"running", "last_tick_at", "last_dispatch_at", "last_error"}


class TestExternalLibrariesProbe:
    def test_counts_enabled_libraries_separately_from_configured(
        self, db_session: Session
    ) -> None:
        db_session.add(ExternalLibrary(name="nas", root_path="/mnt/nas", enabled=True))
        db_session.add(
            ExternalLibrary(name="archive", root_path="/mnt/archive", enabled=False)
        )
        db_session.commit()

        out = health_mod._external_libraries_probe()

        assert out["configured"] == 2
        assert out["enabled"] == 1

    def test_counts_a_running_scan_without_degrading(self, db_session: Session) -> None:
        db_session.add(
            ExternalLibrary(
                name="nas",
                root_path="/mnt/nas",
                last_scan_status=ExternalLibraryScanStatus.RUNNING,
            )
        )
        db_session.commit()

        out = health_mod._external_libraries_probe()

        assert out["running"] == 1
        assert out["ok"] is True, "a scan in progress is not a fault"


class TestSpoolmanProbe:
    def test_reports_the_integration_as_disabled_by_default(
        self, db_session: Session
    ) -> None:
        assert health_mod._spoolman_probe() == {"ok": True, "enabled": False}

    def test_reports_enabled_but_unconfigured(
        self, db_session: Session, spoolman_enabled
    ) -> None:
        spoolman_enabled(base_url=None)

        out = health_mod._spoolman_probe()

        assert out == {"ok": True, "enabled": True, "configured": False}

    def test_reports_the_reachable_servers_version(
        self, db_session: Session, spoolman_enabled, spoolman_server
    ) -> None:
        spoolman_enabled()
        spoolman_server(body={"version": "1.9.0"})

        out = health_mod._spoolman_probe()

        assert out["reachable"] is True
        assert out["version"] == "1.9.0"

    def test_sends_the_stored_api_key(
        self, db_session: Session, spoolman_enabled, spoolman_server
    ) -> None:
        spoolman_enabled(api_key=SPOOLMAN_KEY)
        calls = spoolman_server()

        health_mod._spoolman_probe()

        assert calls[0]["headers"]["Authorization"] == f"Bearer {SPOOLMAN_KEY}"
        assert calls[0]["headers"]["X-Api-Key"] == SPOOLMAN_KEY

    def test_stays_ok_when_the_server_is_unreachable(
        self, db_session: Session, spoolman_enabled, spoolman_server
    ) -> None:
        spoolman_enabled()
        spoolman_server(error=httpx.ConnectError("connection refused"))

        out = health_mod._spoolman_probe()

        assert out["ok"] is True
        assert out["reachable"] is False
        assert out["error"] == "ConnectError"

    def test_treats_a_non_2xx_response_as_unreachable(
        self, db_session: Session, spoolman_enabled, spoolman_server
    ) -> None:
        spoolman_enabled()
        spoolman_server(status_code=500, body={"detail": "boom"})

        out = health_mod._spoolman_probe()

        assert out["reachable"] is False
        assert out["version"] is None
