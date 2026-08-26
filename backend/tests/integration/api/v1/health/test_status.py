"""Defends status at the health API integration boundary.

A regression could hide an unhealthy service or accept an invalid health request.
"""

from __future__ import annotations

from ._hardening_shared import (
    Session,
    TestClient,
    get_config,
    runtime_config,
)


def test_public_health_discloses_only_liveness(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "name": "PrintStash"}


def test_detailed_health_requires_admin_and_reports_release_components(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    denied = client.get("/api/v1/health/details")
    assert denied.status_code == 401

    response = client.get("/api/v1/health/details", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "PrintStash"
    assert body["version"] == get_config().app_version
    assert body["deployment"] == {
        "mode": "single_process",
        "processes": 1,
        "distributed_coordination": False,
    }
    assert body["components"]["database"]["ok"] is True
    assert body["components"]["storage"]["backend"] == "local"
    assert body["components"]["backup"]["s3_configured"] is False
    scheduler = body["components"]["fleet_scheduler"]
    assert scheduler["ok"] is True
    assert "queued" in scheduler["counts"]
    assert "last_tick_at" in scheduler
    providers = body["components"]["printer_providers"]["providers"]
    bambu = next(p for p in providers if p["provider"] == "bambu_lan")
    assert bambu["support_level"] == "beta"
    assert bambu["capabilities"]["can_upload"] is True
    assert bambu["capabilities"]["can_start"] is True
    assert "list_files" in bambu["unsupported_actions"]
    prusalink = next(p for p in providers if p["provider"] == "prusalink")
    assert prusalink["support_level"] == "beta"
    assert prusalink["capabilities"]["can_list_files"] is True
    assert prusalink["capabilities"]["can_send_gcode"] is False
    centauri = next(p for p in providers if p["provider"] == "elegoo_centauri")
    assert centauri["support_level"] == "beta"
    assert centauri["capabilities"]["can_live_status"] is True
    assert centauri["capabilities"]["can_upload"] is True


def test_release_check_requires_admin(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    async def fake_release_status(current_version: str, *, force: bool = False) -> dict:
        assert force is True
        return {
            "status": "up_to_date",
            "current_version": current_version,
            "latest_version": current_version,
            "update_available": False,
            "release_url": "https://example.test/release",
            "published_at": "2026-07-14T10:00:00Z",
            "checked_at": "2026-07-14T11:00:00Z",
        }

    monkeypatch.setattr("app.api.v1.health.get_release_status", fake_release_status)

    denied = client.get("/api/v1/health/releases/latest")
    assert denied.status_code == 401

    response = client.get(
        "/api/v1/health/releases/latest?refresh=true", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "up_to_date"


def test_configured_setup_status_redacts_internal_storage_details(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    config = runtime_config.get_config(db_session)
    config.data_dir = "/secret/vault/files"
    config.s3_bucket = "private-bucket"
    config.s3_endpoint_url = "http://internal-s3:9000"
    db_session.add(config)
    db_session.commit()
    runtime_config.mark_configured(db_session)

    response = client.get("/api/v1/setup/status")

    assert response.status_code == 200
    assert response.json() == {"configured": True, "user_count": 0}
