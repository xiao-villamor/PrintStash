"""Exercise provenance capture identity and field-origin persistence.

Failures mean imported attribution can change or attach to the wrong model.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from app.db.models import (
    ArtifactProvenanceLink,
    File,
    FileType,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelSourceCover,
    ProvenanceCapture,
    StorageDeleteIntent,
    User,
)
from app.schemas.provenance import CaptureManifestV2
from app.services import provenance, storage_deletion, trash
from app.services.storage_backend import StorageBackend, get_backend
from app.services.storage_ownership import (
    UnsafeStorageDeleteError,
    record_creation,
)


def _model(session: Session) -> Model:
    row = Model(name="Bracket", slug="bracket", hash="a" * 64)
    session.add(row)
    session.commit()
    session.refresh(row)
    # The shared SQLite fixture predates provenance tables; clear any rows
    # whose reused in-memory model id survived its legacy teardown list.
    # The legacy fixture's teardown did not know the new dependent tables.
    # Clear them explicitly in this module so reused SQLite ids cannot make a
    # capture history from a prior test look like this model's history.
    for link in session.exec(select(ArtifactProvenanceLink)).all():
        session.delete(link)
    for capture in session.exec(select(ProvenanceCapture)).all():
        session.delete(capture)
    for field in session.exec(select(ModelProvenanceField)).all():
        session.delete(field)
    for source in session.exec(
        select(ModelProvenanceSource).where(ModelProvenanceSource.model_id == row.id)
    ).all():
        session.delete(source)
    session.flush()
    return row


def _capture(*, title: str = "Bracket") -> CaptureManifestV2:
    return CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://Printables.com/model/42?utm_source=test#details",
                "source_item_id": "42",
                "source_revision": None,
                "adapter_version": "printables-v1",
                "fields": {"title": {"value": title, "origin": "confirmed"}},
            },
            "files": [
                {
                    "id": "42:file-a",
                    "name": "part.stl",
                    "file_type": "stl",
                    "size": None,
                }
            ],
            "selected_ids": ["42:file-a"],
        }
    )


def _legacy_capture(*, title: str = "Bracket") -> CaptureManifestV2:
    """The pre-V2 URL identity, as stored before a stable item id arrived."""
    data = _capture(title=title).to_dict()
    data["source"]["source_item_id"] = None
    return CaptureManifestV2.from_dict(data)


def _capture_without_title() -> CaptureManifestV2:
    data = _capture().to_dict()
    data["source"]["fields"].pop("title")
    return CaptureManifestV2.from_dict(data)


__all__ = [
    "ArtifactProvenanceLink",
    "File",
    "FileType",
    "InboxItem",
    "InboxItemResult",
    "InboxItemResultState",
    "MagicMock",
    "Model",
    "ModelProvenanceField",
    "ModelProvenanceSource",
    "ModelSourceCover",
    "ProvenanceCapture",
    "Session",
    "StorageBackend",
    "StorageDeleteIntent",
    "UnsafeStorageDeleteError",
    "User",
    "_capture",
    "_capture_without_title",
    "_legacy_capture",
    "_model",
    "get_backend",
    "json",
    "provenance",
    "pytest",
    "record_creation",
    "select",
    "storage_deletion",
    "trash",
]
