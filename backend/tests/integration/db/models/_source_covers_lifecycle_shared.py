"""Failure-safe cover publishing uses only backend seams, never local paths."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from unittest.mock import MagicMock

import pytest
from PIL import Image
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    BackgroundJob,
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxSourceKind,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    OwnedStorageObject,
    StagingLease,
    StorageObjectState,
    StorageDeleteIntent,
    User,
)
from app.db.session import SQLiteSessionFactory, _set_sqlite_pragmas, get_session_factory
from app.services import inbox, source_covers, staging_leases, trash
from app.services.source_cover_processing import process_source_cover_upload
from app.services.storage_backend import (
    CreationReceipt,
    LocalStorageBackend,
    StorageBackend,
    StorageObjectInfo,
    get_backend,
)
from app.services.storage_ownership import record_creation


def _png(color: str = "navy") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="PNG")
    return output.getvalue()


def _source(session: Session) -> ModelProvenanceSource:
    ident = uuid.uuid4().hex
    model = Model(
        name=f"Cover model {ident}", slug=f"cover-model-{ident}", hash=ident * 2
    )
    session.add(model)
    session.flush()
    source = ModelProvenanceSource(
        model_id=model.id,
        provider="test",
        canonical_url="https://example.test/cover",
        identity_key=uuid.uuid4().hex * 2,
    )
    session.add(source)
    session.flush()
    return source


def _backend() -> MagicMock:
    backend = MagicMock(spec=StorageBackend)
    backend.source_cover_key.side_effect = lambda ident: f"opaque/covers/{ident}.webp"
    return backend


def _receipt(key: str = "opaque/covers/1.webp", token: str = "new") -> CreationReceipt:
    return CreationReceipt(
        key=key, size=10, token=token, backend="fake", namespace="test"
    )


__all__ = [
    "BackgroundJob",
    "CaptureUploadSlot",
    "CaptureUploadSlotState",
    "CreationReceipt",
    "InboxItem",
    "InboxSourceKind",
    "IntegrityError",
    "Model",
    "ModelProvenanceSource",
    "ModelSourceCover",
    "OwnedStorageObject",
    "SQLModel",
    "SQLiteSessionFactory",
    "Session",
    "StagingLease",
    "StorageDeleteIntent",
    "StorageObjectState",
    "StorageObjectInfo",
    "User",
    "_backend",
    "_png",
    "_receipt",
    "_set_sqlite_pragmas",
    "_source",
    "create_engine",
    "event",
    "get_backend",
    "get_session_factory",
    "hashlib",
    "inbox",
    "json",
    "LocalStorageBackend",
    "process_source_cover_upload",
    "pytest",
    "record_creation",
    "select",
    "source_covers",
    "staging_leases",
    "trash",
    "uuid",
]
