"""``persist_artifact`` writes one artifact, or nothing at all.

It used to commit the File row before writing the thumbnail and the Metadata
row. A failure in between (a corrupt image, a full disk) left a committed File
with no metadata — a model that renders but has no print time, filament or cost,
and no error anywhere to explain it.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest
from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import _overlay
from app.db.models import (
    File,
    FileType,
    Metadata,
    Model,
    ModelProvenanceField,
    ProvenanceCapture,
    User,
)
from app.db.session import SQLiteSessionFactory, _set_sqlite_pragmas
from app.services import ingestion, provenance, thumbnail
from app.services.jobs import registry
from app.services.storage_backend import get_backend


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


@pytest.fixture
def model(db_session: Session) -> Model:
    model = Model(name="Bracket", slug="bracket", hash="h" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


def _staged(tmp_path: Path, name: str = "bracket.stl") -> Path:
    staged = tmp_path / name
    staged.write_bytes(b"solid bracket\nendsolid\n")
    return staged


def _persist(db_session: Session, model: Model, staged: Path, **kwargs):
    defaults = dict(
        model=model,
        staged_path=staged,
        original_filename=staged.name,
        file_type=FileType.STL,
        blob_hash="b" * 64,
        meta={"estimated_time_s": 120},
        thumb_bytes=None,
        overwrite_thumbnail=True,
    )
    defaults.update(kwargs)
    return ingestion.persist_artifact(db_session, **defaults)


def _session_factory(session: Session) -> SQLiteSessionFactory:
    engine = session.get_bind()
    assert isinstance(engine, Engine)
    return SQLiteSessionFactory(engine)


def _break_durable_row_set(
    session: Session, model: Model, file_row: File, case: str
) -> None:
    if case == "missing-model":
        session.delete(model)
    elif case == "missing-artifact":
        session.delete(file_row)
    elif case == "missing-metadata":
        metadata = session.exec(
            select(Metadata).where(Metadata.file_id == file_row.id)
        ).one()
        session.delete(metadata)
    else:
        other = Model(name="Other", slug="other", hash="o" * 64)
        session.add(other)
        session.flush()
        assert other.id is not None
        file_row.model_id = other.id
        session.add(file_row)
    session.commit()


__all__ = [
    "Engine",
    "File",
    "FileType",
    "Metadata",
    "Model",
    "ModelProvenanceField",
    "Path",
    "ProvenanceCapture",
    "SQLModel",
    "SQLiteSessionFactory",
    "Session",
    "User",
    "_break_durable_row_set",
    "_persist",
    "_session_factory",
    "_set_sqlite_pragmas",
    "_staged",
    "create_engine",
    "event",
    "hashlib",
    "ingestion",
    "provenance",
    "pytest",
    "registry",
    "select",
    "threading",
    "thumbnail",
]
