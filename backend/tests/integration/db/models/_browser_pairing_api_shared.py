"""Exercise browser pairing ownership, hashing, and single-use persistence.

Failures expose credentials or pairing claims that cross an authorization boundary.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.dialects import postgresql, sqlite
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.api.v1.provider_connections import _claim_limit
from app.core.browser_device_auth import require_browser_import_user
from app.core.time import utcnow
from app.db.models import BrowserDevice, BrowserPairingCode, User
from app.db.session import _set_sqlite_pragmas
from app.services import inbox
from app.services import provider_connections as provider_service
from app.services.auth import create_access_token, hash_password


def _headers(session: Session, username: str) -> dict[str, str]:
    user = User(username=username, hashed_password=hash_password("Password123"))
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.id is not None
    return {
        "Authorization": f"Bearer {create_access_token(user.id, user.username, scope='write')}"
    }


__all__ = [
    "BrowserDevice",
    "BrowserPairingCode",
    "HTTPException",
    "SQLModel",
    "Session",
    "TestClient",
    "User",
    "_claim_limit",
    "_headers",
    "_set_sqlite_pragmas",
    "col",
    "create_engine",
    "event",
    "hash_password",
    "hashlib",
    "inbox",
    "postgresql",
    "provider_service",
    "pytest",
    "require_browser_import_user",
    "select",
    "sqlite",
    "threading",
    "timedelta",
    "utcnow",
]
