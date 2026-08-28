"""A self-contained, file-based vault for the backup tests.

The suite's shared in-memory engine cannot be used here: `create_backup` reads the
SQLite database *as a file* and `restore_backup` writes the file back, so a backup test
needs a real on-disk database and a real storage root. This module owns that harness so
the service tests and the router tests share one definition instead of two that drift.

Fixtures live in ``tests/integration/conftest.py``; the builders below are what they
call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

import app.services.backup as backup
import app.services.storage_backend as storage_backend
from app.core.config import _overlay
from app.db.models import Document, DocumentKind, File, FileType, Model, User
from app.db.session import (
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    override_session_factory,
)
from app.services.auth import create_access_token, hash_password
from app.services.storage_backend import get_backend


@dataclass
class BackupEnv:
    root: Path
    data_dir: Path
    backup_dir: Path
    db_file: Path
    engine: object

    def new_session(self) -> Session:
        return Session(self.engine)


def build_backup_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BackupEnv]:
    """Stand up a file-based vault under *tmp_path* and point the app at it."""
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    backup_dir = tmp_path / "backups"
    db_dir = tmp_path / "db"
    for directory in (data_dir, thumb_dir, backup_dir, db_dir):
        directory.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "vault.sqlite"
    db_url = f"sqlite:///{db_file}"

    _overlay.update(
        {
            "storage_backend": "local",
            "data_dir": data_dir,
            "thumb_dir": thumb_dir,
            "backup_dir": backup_dir,
            "db_url": db_url,
        }
    )

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    override_session_factory(SQLiteSessionFactory(engine))

    # The composition root binds storage after applying the overlay; this is the
    # equivalent isolated composition.
    storage_backend.bind_backend(storage_backend.LocalStorageBackend())
    monkeypatch.setattr(backup, "_backup_s3", None, raising=False)
    # A real restore waits a grace period for in-flight jobs; tests need not pay it.
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


def seed_model_with_blob(
    env: BackupEnv, *, name: str, content: bytes
) -> tuple[int, str]:
    """Create a Model + File row and write the blob. Returns ``(model_id, key)``."""
    slug = name.lower().replace(" ", "-")
    key = get_backend().blob_key(slug, 1, f"{slug}.stl")
    get_backend().write_bytes(content, key)

    sha = hashlib.sha256(content).hexdigest()
    with env.new_session() as session:
        model = Model(name=name, slug=slug, hash=sha)
        session.add(model)
        session.commit()
        session.refresh(model)
        session.add(
            File(
                model_id=model.id,
                path=key,
                original_filename=f"{slug}.stl",
                file_type=FileType.STL,
                version=1,
                size_bytes=len(content),
                sha256=sha,
            )
        )
        session.commit()
        return model.id, key


def seed_document_with_blob(env: BackupEnv, *, name: str, content: bytes) -> str:
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


def backup_admin_headers(
    env: BackupEnv, *, username: str = "backup-admin"
) -> dict[str, str]:
    """A superuser inside the harness's own database, with bearer headers."""
    with env.new_session() as session:
        user = User(
            username=username,
            hashed_password=hash_password("Password123"),
            is_active=True,
            is_superuser=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_access_token(user.id, user.username, scope="admin")
    return {"Authorization": f"Bearer {token}"}


def user_headers_in_env(
    env: BackupEnv, *, username: str = "backup-operator"
) -> dict[str, str]:
    """A signed-in non-superuser inside the harness's own database."""
    with env.new_session() as session:
        user = User(
            username=username,
            hashed_password=hash_password("Password123"),
            is_active=True,
            is_superuser=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}
