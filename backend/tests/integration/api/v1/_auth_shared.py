"""Shared builders for authentication router integration tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import User
from app.services.auth import create_access_token, hash_password


def create_user(
    session: Session,
    username: str,
    *,
    password: str = "Password123",
    is_superuser: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        hashed_password=hash_password(password),
        is_superuser=is_superuser,
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def login(
    client: TestClient,
    username: str,
    *,
    password: str = "Password123",
) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def headers(user: User) -> dict[str, str]:
    scope = "admin" if user.is_superuser else "write"
    token = create_access_token(
        user.id,
        user.username,
        scope=scope,
        auth_version=user.auth_version,
    )
    return {"Authorization": f"Bearer {token}"}
