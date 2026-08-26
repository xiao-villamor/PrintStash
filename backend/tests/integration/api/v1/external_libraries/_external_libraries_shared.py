"""External library (NAS folder mirroring) scan + write-back tests."""

from __future__ import annotations

import shutil
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    ExternalLibrary,
    ExternalLibraryCollectionMode,
    ExternalLibraryScanStatus,
    File,
    Metadata,
    Model,
)
from app.db.scopes import live
from app.services import external_library, runtime_config
from app.services.ingestion import ingest_orca_gcode
from tests.paths import TEST_DATA_DIR

FIXTURE_GCODE = TEST_DATA_DIR / "sample.gcode"


def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    (tmp_path / "thumbs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "staging" / "_incoming").mkdir(parents=True, exist_ok=True)


def _enable_feature(session: Session) -> None:
    runtime_config.set_external_libraries_enabled(session, True)


def _make_library(session: Session, root: Path, **kw) -> ExternalLibrary:
    lib = ExternalLibrary(name="nas", root_path=str(root), **kw)
    session.add(lib)
    session.commit()
    session.refresh(lib)
    return lib


def _drop_gcode(dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    shutil.copy(FIXTURE_GCODE, target)
    return target


__all__ = [
    "ExternalLibraryCollectionMode",
    "ExternalLibraryScanStatus",
    "FIXTURE_GCODE",
    "File",
    "Metadata",
    "Model",
    "Path",
    "Session",
    "_configure_storage",
    "_drop_gcode",
    "_enable_feature",
    "_make_library",
    "_overlay",
    "external_library",
    "ingest_orca_gcode",
    "live",
    "pytest",
    "select",
    "shutil",
    "timedelta",
    "utcnow",
    "uuid",
]
