"""Currency setting round-trips through the config API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import _overlay


def _configure_storage(tmp_path: Path) -> None:
    # PUT /config calls ensure_dirs(); point storage at the test's tmp dir so it
    # doesn't try to mkdir the real /data root.
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    _overlay["backup_dir"] = tmp_path / "backups"


def test_currency_defaults_to_usd(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get("/api/v1/config", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["currency"] == "USD"


def test_currency_can_be_updated(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    _configure_storage(tmp_path)
    resp = client.put("/api/v1/config", json={"currency": "EUR"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["currency"] == "EUR"

    # Persisted across reads.
    assert (
        client.get("/api/v1/config", headers=auth_headers).json()["currency"] == "EUR"
    )


def test_currency_rejects_bad_length(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.put("/api/v1/config", json={"currency": "EURO"}, headers=auth_headers)
    assert resp.status_code == 422


def test_update_rejects_invalid_storage_backend(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.put(
        "/api/v1/config", json={"storage_backend": "ftp"}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert "storage_backend" in resp.json()["detail"]


def test_update_toggles_auto_mark_known_good_and_external_libraries(
    client: TestClient, auth_headers: dict[str, str], tmp_path
) -> None:
    _configure_storage(tmp_path)
    resp = client.put(
        "/api/v1/config",
        json={"auto_mark_known_good": False, "external_libraries_enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["auto_mark_known_good"] is False
    assert body["external_libraries_enabled"] is True


# --------------------------------------------------------------------------- #
# MakerWorld login/status endpoints
# --------------------------------------------------------------------------- #


def test_makerworld_status_defaults_disconnected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get("/api/v1/config/makerworld", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "updated_at": None}


def test_makerworld_token_connects_and_disconnects(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/config/makerworld/token",
        json={"token": "token=abc.def.ghi; other=x"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["connected"] is True

    resp = client.delete("/api/v1/config/makerworld", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["connected"] is False


def test_makerworld_token_rejects_blank(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.post(
        "/api/v1/config/makerworld/token",
        json={"token": "token=   "},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "missing_token"


def test_makerworld_login_needs_email_code(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.makerworld_auth import LoginResult

    result = LoginResult(status="need_email_code", login_token="pending-123")
    with patch("app.api.v1.config.begin_login", new=AsyncMock(return_value=result)):
        resp = client.post(
            "/api/v1/config/makerworld/login",
            json={"account": "user@example.com", "password": "hunter2"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "need_email_code"
    assert body["login_token"] == "pending-123"
    assert body["connected"] is False


def test_makerworld_login_succeeds_immediately(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.makerworld_auth import LoginResult

    result = LoginResult(status="ok", token="a-real-token")
    with patch("app.api.v1.config.begin_login", new=AsyncMock(return_value=result)):
        resp = client.post(
            "/api/v1/config/makerworld/login",
            json={"account": "user@example.com", "password": "hunter2"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["connected"] is True

    status_resp = client.get("/api/v1/config/makerworld", headers=auth_headers)
    assert status_resp.json()["connected"] is True


def test_makerworld_login_rejects_bad_credentials(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.makerworld_auth import MakerWorldAuthError

    with patch(
        "app.api.v1.config.begin_login",
        new=AsyncMock(side_effect=MakerWorldAuthError("invalid_credentials")),
    ):
        resp = client.post(
            "/api/v1/config/makerworld/login",
            json={"account": "user@example.com", "password": "wrong"},
            headers=auth_headers,
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_credentials"


def test_makerworld_verify_succeeds(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.makerworld_auth import LoginResult

    result = LoginResult(status="ok", token="a-real-token")
    with patch("app.api.v1.config.submit_code", new=AsyncMock(return_value=result)):
        resp = client.post(
            "/api/v1/config/makerworld/verify",
            json={"login_token": "pending-123", "code": "123456"},
            headers=auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["connected"] is True


def test_makerworld_verify_rejects_invalid_code(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.makerworld_auth import LoginResult

    result = LoginResult(status="need_email_code", token=None)
    with patch("app.api.v1.config.submit_code", new=AsyncMock(return_value=result)):
        resp = client.post(
            "/api/v1/config/makerworld/verify",
            json={"login_token": "pending-123", "code": "wrong"},
            headers=auth_headers,
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_code"


def test_makerworld_verify_raises_auth_error(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.makerworld_auth import MakerWorldAuthError

    with patch(
        "app.api.v1.config.submit_code",
        new=AsyncMock(side_effect=MakerWorldAuthError("expired_token")),
    ):
        resp = client.post(
            "/api/v1/config/makerworld/verify",
            json={"login_token": "pending-123", "code": "123456"},
            headers=auth_headers,
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "expired_token"
