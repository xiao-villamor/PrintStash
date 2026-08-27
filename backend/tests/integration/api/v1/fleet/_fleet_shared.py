"""Exercise fleet API scheduling, permissions, and persisted job state.

Failures mean callers can observe an unsafe assignment or authorization outcome.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    Printer,
    PrinterPermission,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
    User,
)
from app.services import fleet
from app.services.auth import create_access_token, hash_password
from app.services.printer_provider import PrinterProviderClient


def _provider_builder(provider: PrinterProviderClient):
    return lambda _printer: provider


def _unused_provider_builder(_printer: Printer) -> PrinterProviderClient:
    raise AssertionError("provider construction should not be reached")


def _gcode(session: Session) -> File:
    model = Model(name="Queue cube", slug="queue-cube", hash="a" * 64)
    session.add(model)
    session.commit()
    session.refresh(model)
    artifact = File(
        model_id=model.id,
        path="queue/cube.gcode",
        original_filename="cube.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=42,
        sha256="b" * 64,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def _user_headers(
    db_session: Session, username: str, *, is_superuser: bool = False
) -> dict[str, str]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


def _grant_printer(
    db_session: Session, username: str, printer: Printer, role: PrinterRole
) -> None:
    user = db_session.exec(select(User).where(User.username == username)).one()
    db_session.add(PrinterPermission(user_id=user.id, printer_id=printer.id, role=role))
    db_session.commit()


__all__ = [
    "AsyncMock",
    "Collection",
    "CollectionPermission",
    "CollectionRole",
    "FastAPI",
    "File",
    "FileType",
    "Model",
    "Path",
    "PrintJob",
    "PrintJobState",
    "Printer",
    "PrinterPermission",
    "PrinterRole",
    "PrinterStatus",
    "RoutingStrategy",
    "Session",
    "TestClient",
    "User",
    "_gcode",
    "_grant_printer",
    "_provider_builder",
    "_unused_provider_builder",
    "_user_headers",
    "asyncio",
    "create_access_token",
    "event",
    "fleet",
    "hash_password",
    "patch",
    "select",
    "threading",
    "time",
    "timedelta",
    "utcnow",
]
