"""Backup & restore round-trip tests.

These exercise the local-storage backup path end to end: create an archive
from a populated vault (DB + blobs), then prove a restore brings the database
rows and the stored file bytes back after a simulated disaster.

The shared in-memory test harness can't be used here: ``create_backup`` reads
the SQLite database *as a file* (``_backup_sqlite_copy``) and ``_restore_database``
writes the file back, so these tests stand up a self-contained file-based DB and
a local storage root under ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

import app.services.backup as backup
import app.services.storage_backend as storage_backend
from app.core.config import _overlay
from app.db.models import (
    SENTINEL_FILE_HASH,
    SENTINEL_MODEL_HASH,
    Document,
    DocumentKind,
    File,
    FileType,
    Model,
    OwnedStorageObject,
    Printer,
    PrintJob,
    PrintJobState,
    User,
)
from app.db.session import (
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    override_session_factory,
)
from app.services.auth import create_access_token, hash_password
from app.services.storage_backend import CreationReceipt, get_backend


@dataclass
class BackupEnv:
    root: Path
    data_dir: Path
    backup_dir: Path
    db_file: Path
    engine: object

    def new_session(self) -> Session:
        return Session(self.engine)


@pytest.fixture
def backup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[BackupEnv]:
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    backup_dir = tmp_path / "backups"
    db_dir = tmp_path / "db"
    for d in (data_dir, thumb_dir, backup_dir, db_dir):
        d.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "vault.sqlite"
    db_url = f"sqlite:///{db_file}"

    # Point the effective config at our file-based vault.
    _overlay.update(
        {
            "storage_backend": "local",
            "data_dir": data_dir,
            "thumb_dir": thumb_dir,
            "backup_dir": backup_dir,
            "db_url": db_url,
        }
    )

    # A real on-disk SQLite DB that the session factory and the backup
    # service's file-level reads/writes both target.
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    override_session_factory(SQLiteSessionFactory(engine))

    # The application composition root binds storage after applying the
    # runtime overlay. This fixture owns an equivalent isolated composition.
    storage_backend.bind_backend(storage_backend.LocalStorageBackend())
    monkeypatch.setattr(backup, "_backup_s3", None, raising=False)
    # Real restores wait a grace period for in-flight jobs to finish; tests
    # don't need to pay that wall-clock cost.
    monkeypatch.setattr(backup, "_RESTORE_GRACE_PERIOD_S", 0)

    try:
        yield BackupEnv(
            root=tmp_path,
            data_dir=data_dir,
            backup_dir=backup_dir,
            db_file=db_file,
            engine=engine,
        )
    finally:
        engine.dispose()


def _seed_model_with_blob(
    env: BackupEnv, *, name: str, content: bytes
) -> tuple[int, str]:
    """Create a Model + File row and write the blob through the backend.

    Returns ``(model_id, storage_key)``.
    """
    slug = name.lower().replace(" ", "-")
    key = get_backend().blob_key(slug, 1, f"{slug}.stl")
    get_backend().write_bytes(content, key)

    sha = hashlib.sha256(content).hexdigest()
    with env.new_session() as session:
        model = Model(name=name, slug=slug, hash=sha)
        session.add(model)
        session.commit()
        session.refresh(model)
        f = File(
            model_id=model.id,
            path=key,
            original_filename=f"{slug}.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=len(content),
            sha256=sha,
        )
        session.add(f)
        session.commit()
        return model.id, key


def _seed_document_with_blob(env: BackupEnv, *, name: str, content: bytes) -> str:
    """Create a binary Document row and write its blob. Returns the storage key."""
    with env.new_session() as session:
        doc = Document(name=name, kind=DocumentKind.PDF)
        session.add(doc)
        session.commit()
        session.refresh(doc)
        key = get_backend().document_file_key(doc.id, name)
        get_backend().write_bytes(content, key)
        doc.filename = name
        doc.size_bytes = len(content)
        session.add(doc)
        session.commit()
        return key


def _read_model_names(env: BackupEnv) -> list[str]:
    """Read model names through a brand-new engine so the restored DB file is
    seen, not a connection cached against the pre-restore file."""
    eng = create_engine(
        f"sqlite:///{env.db_file}", connect_args={"check_same_thread": False}
    )
    try:
        with Session(eng) as session:
            return [m.name for m in session.exec(select(Model)).all()]
    finally:
        eng.dispose()


def _auth_headers(env: BackupEnv) -> dict[str, str]:
    with env.new_session() as session:
        user = User(
            username="backup-admin",
            hashed_password=hash_password("Password123"),
            is_active=True,
            is_superuser=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_access_token(user.id, user.username, scope="admin")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Restore round trip
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Audit trail (0.8.5 item 3): backup/restore mutate the filesystem and swap
# the DB file, so they don't flow through the ORM after_flush hook — the
# service writes AuditLog rows explicitly.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Restore gate (item 11: quiesce background loops during restore)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Corrupted / invalid archives (local — no S3 endpoint needed)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Narrow branch closures: S3 key helper, size-mismatch abort, list_backups
# merge/dedup, and the "member present but unreadable" edge cases in
# _read_manifest / _restore_key_map / restore_backup (all local-only, no
# S3 endpoint needed).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# S3 backup destination (independent from vault storage) — needs a real endpoint
# ---------------------------------------------------------------------------

_S3_ENDPOINT = os.environ.get("PRINTSTASH_TEST_S3_ENDPOINT")
requires_s3 = pytest.mark.s3


@pytest.fixture
def backup_s3_env(backup_env: BackupEnv) -> Iterator[BackupEnv]:
    import uuid as _uuid

    bucket = f"printstash-backup-test-{_uuid.uuid4().hex[:12]}"
    _overlay.update(
        {
            "backup_s3_bucket": bucket,
            "backup_s3_endpoint_url": _S3_ENDPOINT,
            "backup_s3_region": "us-east-1",
            "backup_s3_access_key": os.environ.get(
                "PRINTSTASH_TEST_S3_ACCESS_KEY", "printstash"
            ),
            "backup_s3_secret_key": os.environ.get(
                "PRINTSTASH_TEST_S3_SECRET_KEY", "printstash-secret"
            ),
        }
    )
    # First call to _get_backup_s3() lazily creates the bucket via boto3's
    # default behavior is *not* create-on-connect, so create it explicitly.
    s3 = backup._get_backup_s3()
    s3.create_bucket(Bucket=bucket)
    try:
        yield backup_env
    finally:
        for key in (
            s3.get_paginator("list_objects_v2")
            .paginate(Bucket=bucket)
            .search("Contents[].Key")
        ):
            if key:
                s3.delete_object(Bucket=bucket, Key=key)
        s3.delete_bucket(Bucket=bucket)
        for field in (
            "backup_s3_bucket",
            "backup_s3_endpoint_url",
            "backup_s3_region",
            "backup_s3_access_key",
            "backup_s3_secret_key",
        ):
            _overlay.pop(field, None)


# ---------------------------------------------------------------------------
# Router branches (404/409/500) — local backend, no S3 endpoint
# ---------------------------------------------------------------------------

__all__ = [
    "BackupEnv",
    "CreationReceipt",
    "Document",
    "DocumentKind",
    "File",
    "FileType",
    "MagicMock",
    "Model",
    "OwnedStorageObject",
    "Path",
    "PrintJob",
    "PrintJobState",
    "Printer",
    "SENTINEL_FILE_HASH",
    "SENTINEL_MODEL_HASH",
    "Session",
    "TestClient",
    "_auth_headers",
    "_overlay",
    "_read_model_names",
    "_seed_document_with_blob",
    "_seed_model_with_blob",
    "backup",
    "get_backend",
    "json",
    "pytest",
    "requires_s3",
    "select",
    "sqlite3",
    "storage_backend",
    "tarfile",
    "threading",
    "time",
]
