"""Boots the real app lifespan (startup + shutdown), not just handler-level tests.

Every other test in the suite gets its FastAPI ``app`` fixture pre-wired
(``app.state.printer_hub`` set manually, no ``with TestClient(app) as client``),
so ``app/main.py``'s ``lifespan()`` — DB init, storage init, background task
wiring, graceful shutdown — had no direct coverage (58% per the 0.11 audit).
This starts it for real via Starlette's TestClient context-manager protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select
from starlette.requests import Request as StarletteRequest

import app.main as app_main
from app.core.config import _overlay
from app.db.models import (
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    Model,
    ModelProvenanceSource,
    OwnedStorageObject,
    Printer,
    PrinterProvider,
    PrinterStatus,
    StagingLease,
    User,
)
from app.services import storage_backend
from app.services.auth import create_access_token, hash_password
from app.services.realtime import InProcessBus


@pytest.fixture
def _local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _overlay.update(
        {
            "storage_backend": "local",
            "data_dir": tmp_path / "files",
            "thumb_dir": tmp_path / "thumbs",
            "backup_dir": tmp_path / "backups",
            "staging_dir": tmp_path / "staging",
        }
    )
    monkeypatch.setattr(storage_backend, "_backend", None)
    yield
    for field in (
        "storage_backend",
        "data_dir",
        "thumb_dir",
        "backup_dir",
        "staging_dir",
    ):
        _overlay.pop(field, None)


async def _done() -> None:
    pass


def _fake_request(
    path: str = "/x", headers: dict[str, str] | None = None
) -> StarletteRequest:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "query_string": b"",
    }
    return StarletteRequest(scope)


__all__ = [
    "BytesIO",
    "CaptureUploadSlot",
    "CaptureUploadSlotState",
    "Image",
    "InProcessBus",
    "InboxItem",
    "InboxItemState",
    "InboxSourceKind",
    "Model",
    "ModelProvenanceSource",
    "OwnedStorageObject",
    "Path",
    "Printer",
    "PrinterProvider",
    "PrinterStatus",
    "StagingLease",
    "TestClient",
    "User",
    "_done",
    "_fake_request",
    "_overlay",
    "app_main",
    "asyncio",
    "create_access_token",
    "hash_password",
    "hashlib",
    "logging",
    "pytest",
    "select",
]
