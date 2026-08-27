"""API tests for /admin — user management, resource GC/restore, audit log.

No existing test file for this module before this session (audit noted 64%
coverage on user/role management error branches, 165-220).
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.v1 import admin as admin_api
from app.core.time import utcnow
from app.db.models import AuditLog, Collection, File, FileType, Model, Tag, User
from app.schemas.auth import UserUpdate
from app.services.audit import install_audit_listeners
from app.services.auth import create_access_token, hash_password


def _user(
    session: Session,
    username: str,
    *,
    superuser: bool = True,
    active: bool = True,
) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=active,
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
    "AuditLog",
    "Collection",
    "File",
    "FileType",
    "HTTPException",
    "Model",
    "SQLModel",
    "Session",
    "Tag",
    "TestClient",
    "UserUpdate",
    "_headers",
    "_user",
    "admin_api",
    "create_engine",
    "install_audit_listeners",
    "pytest",
    "select",
    "threading",
    "time",
    "utcnow",
]
