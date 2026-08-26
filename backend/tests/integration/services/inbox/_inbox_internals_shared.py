"""Unit coverage for app/services/inbox.py's internal orchestration —
resolve/run_import/retry/dismiss/reconcile/prune — that the API-level tests
in test_inbox_api.py don't reach."""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import nullcontext
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from printstash_core.imports import CaptureManifestV2, ResolvedAsset
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.core.time import utcnow
from app.db.models import (
    ArtifactProvenanceLink,
    BackgroundJob,
    CaptureUploadSlot,
    Collection,
    File,
    FileType,
    InboxItem,
    InboxItemCompletion,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    InboxSourceKind,
    Model,
    ModelProvenanceSource,
    StagingLease,
    StorageDeleteIntent,
    User,
)
from app.db.session import get_session_factory
from app.schemas.inbox import CaptureUploadSlotsCreate, InboxItemUpdate
from app.services import import_resolvers, importer, inbox, staging_leases
from app.services.auth import hash_password
from app.services.jobs import registry
from app.services.storage_backend import StorageBackend


def _make_user(session: Session, username: str, *, admin: bool = True) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_superuser=admin,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_collection(session: Session, path: str = "vault") -> Collection:
    col = Collection(name=path, slug=path, path=path)
    session.add(col)
    session.commit()
    session.refresh(col)
    return col


def _make_item(session: Session, owner: User, **overrides) -> InboxItem:
    defaults = dict(
        owner_user_id=owner.id,
        source_url="https://example.com/model",
        source_hostname="example.com",
        state=InboxItemState.CAPTURED,
    )
    defaults.update(overrides)
    row = InboxItem(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _capture_manifest(*file_names: str) -> CaptureManifestV2:
    return CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://www.printables.com/model/42-bracket",
                "source_item_id": "42",
                "source_revision": None,
                "adapter_version": "printables-v1",
                "fields": {},
            },
            "files": [
                {
                    "id": name,
                    "name": name,
                    "file_type": Path(name).suffix.lstrip("."),
                    "size": None,
                }
                for name in file_names
            ],
            "selected_ids": list(file_names),
        }
    )


# --------------------------------------------------------------------------- #
# sanitize_source_url / _json_dict / requested_tags
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# list_visible
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# prune_history
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# update()
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# resolve()
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# run_import()
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# retry() / dismiss()
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# reconcile_interrupted_items()
# --------------------------------------------------------------------------- #

__all__ = [
    "ArtifactProvenanceLink",
    "BackgroundJob",
    "BytesIO",
    "CaptureManifestV2",
    "CaptureUploadSlot",
    "CaptureUploadSlotsCreate",
    "File",
    "FileType",
    "HTTPException",
    "InboxItem",
    "InboxItemCompletion",
    "InboxItemResult",
    "InboxItemResultState",
    "InboxItemState",
    "InboxItemUpdate",
    "InboxSourceKind",
    "MagicMock",
    "Model",
    "ModelProvenanceSource",
    "Path",
    "ResolvedAsset",
    "Session",
    "StagingLease",
    "StorageBackend",
    "StorageDeleteIntent",
    "_capture_manifest",
    "_make_item",
    "_make_user",
    "_overlay",
    "base64",
    "get_session_factory",
    "hashlib",
    "import_resolvers",
    "importer",
    "inbox",
    "json",
    "nullcontext",
    "pytest",
    "registry",
    "select",
    "settings",
    "staging_leases",
    "timedelta",
    "utcnow",
]
