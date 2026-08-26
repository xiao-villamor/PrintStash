"""API coverage for the Spoolman integration (superuser-only)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_requires_superuser(client: TestClient):
    assert client.get("/api/v1/spoolman").status_code == 401


def test_status_defaults_disabled(client: TestClient, auth_headers):
    body = client.get("/api/v1/spoolman", headers=auth_headers).json()
    assert body["enabled"] is False
    assert body["base_url"] is None
    assert body["has_api_key"] is False
    # Disabled means no network probe runs.
    assert body["connected"] is False


def test_config_roundtrip_and_masks_key(client: TestClient, auth_headers):
    resp = client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://spoolman.local:7912", "api_key": "secret"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["base_url"] == "http://spoolman.local:7912"
    # The key itself is never returned; only its presence.
    assert body["has_api_key"] is True
    assert "secret" not in resp.text


def test_update_preserves_key_when_masked(client: TestClient, auth_headers):
    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://spoolman.local:7912", "api_key": "secret"},
        headers=auth_headers,
    )
    # Re-sending the mask must not wipe the stored key.
    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://spoolman.local:7912", "api_key": "********"},
        headers=auth_headers,
    )
    body = client.get("/api/v1/spoolman", headers=auth_headers).json()
    assert body["has_api_key"] is True


def test_spools_empty_when_disabled(client: TestClient, auth_headers):
    # Even with a base URL set, a disabled integration returns no inventory and
    # makes no network call.
    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://spoolman.local:7912"},
        headers=auth_headers,
    )
    resp = client.get("/api/v1/spoolman/spools", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_enable_toggle(client: TestClient, auth_headers):
    client.put("/api/v1/spoolman", json={"enabled": True}, headers=auth_headers)
    assert (
        client.get("/api/v1/spoolman", headers=auth_headers).json()["enabled"] is True
    )


def test_test_connection_probes_typed_values(
    client: TestClient, auth_headers, monkeypatch
):
    # /test should probe the values posted from the form, not just saved config,
    # so a connection can be verified before Save.
    import app.api.v1.spoolman as mod

    captured: dict = {}

    async def fake_probe(c):
        captured["base_url"] = c.base_url
        captured["api_key"] = c.api_key
        return {
            "connected": True,
            "version": "1.2.3",
            "error": None,
            "native_hook_detected": False,
        }

    monkeypatch.setattr(mod, "_probe", fake_probe)
    resp = client.post(
        "/api/v1/spoolman/test",
        json={"base_url": "http://typed:7912"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == "1.2.3"
    assert captured["base_url"] == "http://typed:7912"


def test_test_connection_preserves_saved_key_on_mask(
    client: TestClient, auth_headers, monkeypatch
):
    import app.api.v1.spoolman as mod

    captured: dict = {}

    async def fake_probe(c):
        captured["api_key"] = c.api_key
        return {
            "connected": True,
            "version": None,
            "error": None,
            "native_hook_detected": False,
        }

    monkeypatch.setattr(mod, "_probe", fake_probe)
    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://s:7912", "api_key": "secret"},
        headers=auth_headers,
    )
    resp = client.post(
        "/api/v1/spoolman/test",
        json={"base_url": "http://s:7912", "api_key": "********"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert captured["api_key"] == "secret"


def test_test_connection_requires_base_url(client: TestClient, auth_headers):
    assert (
        client.post("/api/v1/spoolman/test", json={}, headers=auth_headers).status_code
        == 400
    )


def test_test_connection_uses_typed_api_key_override(
    client: TestClient, auth_headers, monkeypatch
):
    import app.api.v1.spoolman as mod

    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://s:7912", "api_key": "old-key"},
        headers=auth_headers,
    )

    captured: dict = {}

    async def fake_probe(c):
        captured["api_key"] = c.api_key
        return {
            "connected": True,
            "version": None,
            "error": None,
            "native_hook_detected": False,
        }

    monkeypatch.setattr(mod, "_probe", fake_probe)
    resp = client.post(
        "/api/v1/spoolman/test",
        json={"api_key": "brand-new-key"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert captured["api_key"] == "brand-new-key"


def test_probe_reports_connected_and_native_hook(
    client: TestClient, auth_headers, monkeypatch
):
    """Exercises the real ``_probe`` helper (not mocked away), through the
    status endpoint's live-probe branch."""
    import app.services.spoolman as spoolman_mod

    async def fake_health_check(self):
        return {"version": "1.9.0"}

    async def fake_active_spool(self):
        return 42  # a truthy spool id -> native_hook_detected True

    monkeypatch.setattr(spoolman_mod.SpoolmanClient, "health_check", fake_health_check)
    monkeypatch.setattr(spoolman_mod.SpoolmanClient, "active_spool", fake_active_spool)

    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://s:7912", "enabled": True},
        headers=auth_headers,
    )
    resp = client.get("/api/v1/spoolman", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["version"] == "1.9.0"
    assert body["native_hook_detected"] is True


def test_probe_reports_error_on_spoolman_error(
    client: TestClient, auth_headers, monkeypatch
):
    import app.services.spoolman as spoolman_mod
    from app.services.spoolman import SpoolmanError

    async def fake_health_check(self):
        raise SpoolmanError("connection refused")

    monkeypatch.setattr(spoolman_mod.SpoolmanClient, "health_check", fake_health_check)

    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://s:7912", "enabled": True},
        headers=auth_headers,
    )
    resp = client.get("/api/v1/spoolman", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert "connection refused" in body["error"]


def test_write_toggles_roundtrip(client: TestClient, auth_headers):
    resp = client.put(
        "/api/v1/spoolman",
        json={"write_enabled": False, "write_force": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["write_enabled"] is False
    assert body["write_force"] is True


def test_enable_triggers_initial_filament_sync(
    client: TestClient, auth_headers, monkeypatch
):
    import app.api.v1.spoolman as mod
    from app.services.filament_sync import SyncResult

    called = {"n": 0}

    async def fake_sync(session):
        called["n"] += 1
        return SyncResult(created=3)

    monkeypatch.setattr(mod.filament_sync, "sync_from_spoolman", fake_sync)
    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://s:7912"},
        headers=auth_headers,
    )
    resp = client.put("/api/v1/spoolman", json={"enabled": True}, headers=auth_headers)
    assert resp.status_code == 200
    assert called["n"] == 1

    # Re-enabling (already enabled) must not trigger a second sync.
    resp2 = client.put("/api/v1/spoolman", json={"enabled": True}, headers=auth_headers)
    assert resp2.status_code == 200
    assert called["n"] == 1


def test_enable_sync_failure_does_not_fail_the_save(
    client: TestClient, auth_headers, monkeypatch
):
    import app.api.v1.spoolman as mod
    from app.services.spoolman import SpoolmanError

    async def fake_sync(session):
        raise SpoolmanError("unreachable")

    monkeypatch.setattr(mod.filament_sync, "sync_from_spoolman", fake_sync)
    resp = client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://s:7912", "enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_sync_filaments_endpoint_success(client: TestClient, auth_headers, monkeypatch):
    import app.api.v1.spoolman as mod
    from app.services.filament_sync import SyncResult

    async def fake_sync(session):
        return SyncResult(created=1, updated=2, adopted=3, unlinked=4)

    monkeypatch.setattr(mod.filament_sync, "sync_from_spoolman", fake_sync)
    resp = client.post("/api/v1/spoolman/sync-filaments", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == {"created": 1, "updated": 2, "adopted": 3, "unlinked": 4}


def test_sync_filaments_endpoint_error(client: TestClient, auth_headers, monkeypatch):
    import app.api.v1.spoolman as mod
    from app.services.spoolman import SpoolmanError

    async def fake_sync(session):
        raise SpoolmanError("spoolman disabled")

    monkeypatch.setattr(mod.filament_sync, "sync_from_spoolman", fake_sync)
    resp = client.post("/api/v1/spoolman/sync-filaments", headers=auth_headers)

    assert resp.status_code == 400
    assert "spoolman disabled" in resp.json()["detail"]


def test_list_spools_returns_flattened_records(
    client: TestClient, auth_headers, monkeypatch
):
    import app.api.v1.spoolman as mod

    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://s:7912", "enabled": True},
        headers=auth_headers,
    )

    async def fake_list_spools(self, *, include_archived=False):
        return [
            {
                "id": 7,
                "remaining_weight": 500.0,
                "used_weight": 250.0,
                "archived": False,
                "location": "CANVAS-1",
                "filament": {
                    "id": 3,
                    "name": "PLA Red",
                    "material": "PLA",
                    "color_hex": "ff0000",
                    "vendor": {"name": "Acme"},
                },
            },
            # Entry with no id must be filtered out.
            {"id": None, "filament": {}},
        ]

    monkeypatch.setattr(mod.SpoolmanClient, "list_spools", fake_list_spools)
    resp = client.get("/api/v1/spoolman/spools", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == 7
    assert body[0]["filament_name"] == "PLA Red"
    assert body[0]["vendor_name"] == "Acme"
    assert body[0]["location"] == "CANVAS-1"


def test_list_spools_degrades_on_spoolman_error(
    client: TestClient, auth_headers, monkeypatch
):
    import app.api.v1.spoolman as mod
    from app.services.spoolman import SpoolmanError

    client.put(
        "/api/v1/spoolman",
        json={"base_url": "http://s:7912", "enabled": True},
        headers=auth_headers,
    )

    async def fake_list_spools(self, *, include_archived=False):
        raise SpoolmanError("timed out")

    monkeypatch.setattr(mod.SpoolmanClient, "list_spools", fake_list_spools)
    resp = client.get("/api/v1/spoolman/spools", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json() == []
