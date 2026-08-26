"""API-level tests for collection/tag endpoints — error branches and
merge/rename/permission paths not covered by
tests/integration/services/taxonomy/test_taxonomy.py (service-only) or
tests/integration/services/rbac/test_collection_rbac.py (RBAC-focused)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import CollectionPermission, CollectionRole, Model, User
from app.services import taxonomy
from app.services.auth import create_access_token, hash_password
from app.services.storage_backend import get_backend


def _user(session: Session, username: str, *, superuser: bool = False) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=superuser,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _headers(user: User) -> dict[str, str]:
    scope = "admin" if user.is_superuser else "write"
    token = create_access_token(user.id, user.username, scope=scope)
    return {"Authorization": f"Bearer {token}"}


__all__ = [
    "CollectionPermission",
    "CollectionRole",
    "Model",
    "Path",
    "Session",
    "TestClient",
    "_headers",
    "_user",
    "get_backend",
    "hashlib",
    "taxonomy",
]
