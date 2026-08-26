"""Tests for Printers API router (FastAPI TestClient)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect

from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    Printer,
    PrinterFile,
    PrinterPermission,
    PrinterProvider,
    PrinterRole,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    User,
)
from app.services.auth import create_access_token, hash_password
from app.services.printer_jobs import PrinterJobError
from app.services.printer_provider import ProviderError


def _user_headers(
    db_session: Session,
    username: str,
    *,
    is_superuser: bool = False,
    scope: str = "write",
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
    token = create_access_token(user.id, user.username, scope=scope)
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
    "File",
    "FileType",
    "Model",
    "PrintJob",
    "PrintJobState",
    "Printer",
    "PrinterFile",
    "PrinterJobError",
    "PrinterProvider",
    "PrinterRole",
    "PrinterStatus",
    "ProviderError",
    "Session",
    "TestClient",
    "User",
    "WebSocketDisconnect",
    "_grant_printer",
    "_user_headers",
    "asyncio",
    "patch",
    "pytest",
    "replace",
    "select",
]
