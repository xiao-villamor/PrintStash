"""GC deletes exact row-owned keys and never infers ownership from directory walks.

Regression pack for discovery-based deletion: a configured ``data_dir`` may be
a mistakenly mounted user library, so an unclaimed path is never enough proof
that PrintStash may delete it.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    Collection,
    Document,
    DocumentKind,
    File,
    FileType,
    Model,
    ShareLink,
    StorageDeleteIntent,
    User,
    VaultAuditFinding,
    VaultAuditFindingState,
    VaultAuditMode,
    VaultAuditRun,
)
from app.services import trash
from app.services.storage_backend import ObjectIdentity, StorageCapabilities, get_backend
from app.services.storage_ownership import record_creation
from app.services.trash import _cleanup_orphan_blobs, gc_soft_deleted


@pytest.fixture
def storage(tmp_path: Path):
    _overlay["storage_backend"] = "local"
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    (tmp_path / "files").mkdir()
    (tmp_path / "thumbs").mkdir()
    yield get_backend()
    for key in ("storage_backend", "data_dir", "thumb_dir"):
        _overlay.pop(key, None)


def _write(key: str, data: bytes = b"x") -> str:
    p = Path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return key


def _owned_write(session: Session, storage, key: str, data: bytes = b"x") -> str:
    receipt = storage.create_bytes(data, key)
    record_creation(session, receipt, object_kind="test")
    session.commit()
    return key


def _model_with_file(session: Session, storage, slug: str) -> File:
    model = Model(name=slug, slug=slug, hash=f"hash-{slug}")
    session.add(model)
    session.commit()
    session.refresh(model)
    key = _owned_write(session, storage, storage.blob_key(slug, 1, f"{slug}.stl"))
    f = File(
        model_id=model.id,
        path=key,
        original_filename=f"{slug}.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=1,
        sha256=f"sha-{slug}",
    )
    session.add(f)
    session.commit()
    session.refresh(f)
    return f


def _binary_document(session: Session, storage, name: str = "manual.pdf") -> Document:
    doc = Document(name=name, kind=DocumentKind.PDF)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    doc.filename = name
    doc.size_bytes = 1
    session.add(doc)
    session.commit()
    _owned_write(session, storage, storage.document_file_key(doc.id, name))
    return doc


__all__ = [
    "Collection",
    "Document",
    "DocumentKind",
    "File",
    "FileType",
    "Model",
    "Path",
    "Session",
    "ShareLink",
    "StorageDeleteIntent",
    "ObjectIdentity",
    "StorageCapabilities",
    "User",
    "VaultAuditFinding",
    "VaultAuditFindingState",
    "VaultAuditMode",
    "VaultAuditRun",
    "_binary_document",
    "_cleanup_orphan_blobs",
    "_model_with_file",
    "_overlay",
    "_owned_write",
    "_write",
    "gc_soft_deleted",
    "get_backend",
    "hashlib",
    "pytest",
    "select",
    "timedelta",
    "trash",
    "utcnow",
]
