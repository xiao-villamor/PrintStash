"""Exercise inbox ownership, capture slots, and lifecycle persistence.

Failures expose cross-owner data or an invalid durable transition.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import timedelta
from io import BytesIO
from typing import cast

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.requests import ClientDisconnect, Request

from app.api.v1 import inbox as inbox_api
from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    BrowserDevice,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    Collection,
    File,
    FileType,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    StagingLease,
    StorageDeleteIntent,
    User,
)
from app.db.session import get_session_factory
from app.schemas.inbox import CaptureUploadSlotsCreate, InboxImportRequest
from app.services import inbox
from app.services.auth import create_access_token, hash_password
from app.services.source_covers import SourceCoverWrite
from app.services.storage_backend import CreationReceipt
from app.services.storage_deletion import process_storage_delete_intents


def _capture_source(
    *,
    provider: str = "makerworld",
    canonical_url: str = "https://makerworld.com/en/models/1234-widget",
) -> dict:
    return {
        "provider": provider,
        "canonical_url": canonical_url,
        "source_item_id": "1234",
        "source_revision": None,
        "adapter_version": "extension-v1",
        "fields": {"title": {"value": "Widget", "origin": "confirmed"}},
        "tags": [],
    }


def _headers(session: Session, username: str, *, admin: bool = False) -> dict[str, str]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=admin,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "Authorization": f"Bearer {create_access_token(user.id, user.username, scope='admin' if admin else 'write')}"
    }


def _slot_payload(data: bytes = b"slot-owned") -> CaptureUploadSlotsCreate:
    return CaptureUploadSlotsCreate.model_validate(
        {
            "source_url": "https://makerworld.com/en/models/1234-widget",
            "capture_source": _capture_source(),
            "files": [
                {
                    "id": "widget.3mf",
                    "filename": "widget.3mf",
                    "media_type": "application/octet-stream",
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            ],
        }
    )


def _make_item(db_session: Session, owner: User, **overrides) -> InboxItem:
    defaults = dict(
        owner_user_id=owner.id,
        source_url="https://example.com/model",
        source_hostname="example.com",
        state=InboxItemState.CAPTURED,
    )
    defaults.update(overrides)
    row = InboxItem(**defaults)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _user(db_session: Session, username: str, *, admin: bool = True) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=admin,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


class _BackgroundTaskRecorder:
    def __init__(self) -> None:
        self.tasks: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def add_task(self, function: object, *args: object, **kwargs: object) -> None:
        self.tasks.append((function, args, kwargs))


__all__ = [
    "BackgroundJob",
    "BackgroundTasks",
    "BrowserDevice",
    "BytesIO",
    "CaptureUploadSlot",
    "CaptureUploadSlotState",
    "CaptureUploadSlotsCreate",
    "ClientDisconnect",
    "Collection",
    "CreationReceipt",
    "File",
    "FileType",
    "HTTPException",
    "InboxImportRequest",
    "InboxItem",
    "InboxItemResult",
    "InboxItemResultState",
    "InboxItemState",
    "Model",
    "ModelProvenanceSource",
    "ModelSourceCover",
    "Request",
    "Session",
    "SourceCoverWrite",
    "StagingLease",
    "StorageDeleteIntent",
    "TestClient",
    "User",
    "_BackgroundTaskRecorder",
    "_capture_source",
    "_headers",
    "_make_item",
    "_overlay",
    "_slot_payload",
    "_user",
    "cast",
    "create_access_token",
    "get_session_factory",
    "hash_password",
    "hashlib",
    "inbox",
    "inbox_api",
    "io",
    "json",
    "process_storage_delete_intents",
    "pytest",
    "select",
    "timedelta",
    "utcnow",
    "uuid",
]
