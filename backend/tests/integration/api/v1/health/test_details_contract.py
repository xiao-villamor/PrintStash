"""Operational health exposes bounded readiness only to administrators."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import File, FileType, Model
from app.db.session import get_session_factory
from app.services import runtime_config

from .._auth_shared import create_user, headers


@pytest.fixture(autouse=True)
def _use_file_backed_database(file_backed_integration_db: None) -> None:
    """Give nested readiness probes independent production-like connections."""


def _details(client: TestClient, auth_headers: dict[str, str]) -> dict:
    response = client.get("/api/v1/health/details", headers=auth_headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_exposes_operational_health_to_a_superuser(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _details(client, auth_headers)

    assert body["status"] == "ok"
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


def test_reports_healthy_database_readiness(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    with get_session_factory().session() as session:
        model = Model(
            name="Health probe model",
            slug="health-probe-model",
            hash="h" * 64,
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        session.add(
            File(
                model_id=model.id,
                path="health-probe-model/health-probe.stl",
                original_filename="health-probe.stl",
                file_type=FileType.STL,
                size_bytes=12,
                sha256="f" * 64,
            )
        )
        session.commit()

    body = _details(client, auth_headers)

    assert body["components"]["database"]["ok"] is True
    assert body["components"]["database"]["counts"] == {
        "models": 1,
        "files": 1,
        "printers": 0,
        "print_jobs": 0,
    }


def test_reports_healthy_storage_readiness(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _details(client, auth_headers)

    assert body["components"]["storage"]["ok"] is True
    assert body["components"]["storage"]["backend"] == "local"


def test_reports_backup_readiness(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _details(client, auth_headers)

    assert body["components"]["backup"]["ok"] is True
    assert body["components"]["backup"]["local_count"] == 0


def test_reports_supported_single_process_topology(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _details(client, auth_headers)

    assert body["deployment"] == {
        "mode": "single_process",
        "processes": 1,
        "distributed_coordination": False,
    }


def test_reports_background_scheduler_state(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _details(client, auth_headers)

    scheduler = body["components"]["fleet_scheduler"]
    assert scheduler["ok"] is True
    assert set(scheduler) == {
        "ok",
        "counts",
        "running",
        "last_tick_at",
        "last_dispatch_at",
        "last_error",
    }


def test_reports_configured_provider_readiness(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _details(client, auth_headers)

    providers = body["components"]["printer_providers"]
    assert providers["ok"] is True
    assert {item["provider"] for item in providers["providers"]} == {
        "moonraker",
        "bambu_lan",
        "prusalink",
        "elegoo_centauri",
        "octoprint",
    }


def test_redacts_secrets_from_operational_health(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    secrets = (
        "health-storage-access-secret",
        "health-storage-secret-key",
        "health-backup-access-secret",
        "health-backup-secret-key",
        "health-spoolman-secret",
    )
    _overlay["s3_access_key"] = secrets[0]
    _overlay["s3_secret_key"] = secrets[1]
    _overlay["backup_s3_access_key"] = secrets[2]
    _overlay["backup_s3_secret_key"] = secrets[3]
    runtime_config.set_spoolman_enabled(db_session, True)
    runtime_config.set_spoolman_config(
        db_session,
        base_url="http://spoolman.invalid.test",
        api_key=secrets[4],
    )

    response = client.get("/api/v1/health/details", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert all(secret not in response.text for secret in secrets)


def test_denies_a_non_superuser_from_operational_health(
    client: TestClient, db_session: Session
) -> None:
    caller = create_user(db_session, "health-regular-user")

    response = client.get("/api/v1/health/details", headers=headers(caller))

    assert response.status_code == 403, response.text


def test_denies_an_unauthenticated_caller_from_operational_health(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/health/details")

    assert response.status_code == 401, response.text
