"""Integration coverage for the MakerWorld compatibility boundary."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import User
from app.services.auth import create_access_token, hash_password


def _regular_headers(session: Session, username: str) -> dict[str, str]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


def test_makerworld_status_defaults_disconnected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/api/v1/config/makerworld", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"connected": False, "updated_at": None}


def test_makerworld_login_requires_extension(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/config/makerworld/login",
        json={"account": "user@example.com", "password": "fake-password"},
        headers=auth_headers,
    )

    assert response.status_code == 410
    assert response.json()["detail"] == "makerworld_extension_required"


def test_makerworld_login_failure_omits_the_submitted_password(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/config/makerworld/login",
        json={"account": "user@example.com", "password": "fake-password"},
        headers=auth_headers,
    )

    assert "fake-password" not in response.text


def test_makerworld_verification_requires_extension(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/config/makerworld/verify",
        json={"login_token": "fake-login-token", "code": "123456"},
        headers=auth_headers,
    )

    assert response.status_code == 410
    assert response.json()["detail"] == "makerworld_extension_required"


def test_makerworld_verification_failure_omits_the_submitted_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/config/makerworld/verify",
        json={"login_token": "fake-login-token", "code": "123456"},
        headers=auth_headers,
    )

    assert "fake-login-token" not in response.text


def test_makerworld_token_requires_extension(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/config/makerworld/token",
        json={"token": "fake-legacy-token"},
        headers=auth_headers,
    )

    assert response.status_code == 410
    assert response.json()["detail"] == "makerworld_extension_required"
    assert "fake-legacy-token" not in response.text


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        pytest.param(
            "/api/v1/config/makerworld/login",
            {"account": "", "password": "password"},
            id="login-account",
        ),
        pytest.param(
            "/api/v1/config/makerworld/login",
            {"account": "user@example.com", "password": ""},
            id="login-password",
        ),
    ],
)
def test_validates_makerworld_login_fields(
    client: TestClient,
    auth_headers: dict[str, str],
    path: str,
    payload: dict[str, str],
) -> None:
    response = client.post(path, json=payload, headers=auth_headers)

    assert response.status_code == 422, response.text


def test_rejects_an_empty_makerworld_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/config/makerworld/token",
        json={"token": ""},
        headers=auth_headers,
    )

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"login_token": "", "code": "123456"}, id="login-token"),
        pytest.param({"login_token": "pending", "code": ""}, id="code"),
    ],
)
def test_validates_makerworld_verification_fields(
    client: TestClient,
    auth_headers: dict[str, str],
    payload: dict[str, str],
) -> None:
    response = client.post(
        "/api/v1/config/makerworld/verify",
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == 422, response.text


def test_makerworld_disconnect_remains_compatible(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.delete("/api/v1/config/makerworld", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"connected": False, "updated_at": None}


def test_repeated_makerworld_disconnect_is_idempotent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    first = client.delete("/api/v1/config/makerworld", headers=auth_headers)
    assert first.status_code == 200, first.text

    second = client.delete("/api/v1/config/makerworld", headers=auth_headers)

    assert second.status_code == 200, second.text
    assert second.json() == {"connected": False, "updated_at": None}


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        pytest.param("get", "/api/v1/config/makerworld", None, id="status"),
        pytest.param(
            "post",
            "/api/v1/config/makerworld/login",
            {"account": "user@example.com", "password": "fake-password"},
            id="login",
        ),
        pytest.param(
            "post",
            "/api/v1/config/makerworld/verify",
            {"login_token": "pending", "code": "123456"},
            id="verify",
        ),
        pytest.param(
            "post",
            "/api/v1/config/makerworld/token",
            {"token": "fake-token"},
            id="token",
        ),
        pytest.param("delete", "/api/v1/config/makerworld", None, id="disconnect"),
    ],
)
def test_denies_a_non_superuser_from_makerworld_endpoints(
    client: TestClient,
    db_session: Session,
    method: str,
    path: str,
    payload: dict[str, str] | None,
) -> None:
    headers = _regular_headers(db_session, f"makerworld-{method}-{path[-5:]}")

    response = client.request(method, path, json=payload, headers=headers)

    assert response.status_code == 403, response.text
