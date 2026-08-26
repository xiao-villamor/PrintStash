"""End-to-end / real-use-case tests for NAS folder mirroring (external libraries).

These complement the focused unit tests in ``test_external_libraries.py`` and
exercise the safety invariants and full workflows that decide whether the
feature is production-safe:

* **NAS bytes are sacred** — neither trash hard-delete nor scheduled GC
  may ever touch a user's original files (only vault-owned thumbnails/rows go).
* **Write-back never overwrites** an existing file on the NAS.
* Revisions follow their model back into its library.
* Real folder shapes: mixed mesh + g-code, deep nested folders → collection
  hierarchy, SINGLE vs MIRROR collection modes, files moved within the share.
* Reconcile resilience: an mtime-only touch is a no-op; one bad file does not
  abort the whole scan.
* The periodic scheduler picks the right libraries, and the public API drives a
  background scan to completion.

A real on-disk folder of open-source models/g-codes can be pointed at via the
``PRINTSTASH_TEST_NAS_DIR`` env var to run ``test_scan_real_world_folder``.
"""

from __future__ import annotations

import os
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
    FileType,
    Model,
)
from app.db.scopes import live
from app.services import external_library, runtime_config, taxonomy, trash
from app.services.ingestion import add_gcode_revision_to_model, ingest_orca_gcode
from app.services.jobs import registry
from tests.paths import REPO_ROOT, TEST_DATA_DIR

FIXTURE_GCODE = TEST_DATA_DIR / "sample.gcode"

# A small but valid ASCII-STL cube (a real mesh trimesh can parse + thumbnail).
_CUBE_STL = b"""solid cube
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 1 1 0
vertex 1 0 0
endloop
endfacet
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 0 1 0
vertex 1 1 0
endloop
endfacet
facet normal 0 0 1
outer loop
vertex 0 0 1
vertex 1 0 1
vertex 1 1 1
endloop
endfacet
facet normal 0 0 1
outer loop
vertex 0 0 1
vertex 1 1 1
vertex 0 1 1
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 1 0 0
vertex 1 0 1
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 1 0 1
vertex 0 0 1
endloop
endfacet
facet normal 1 0 0
outer loop
vertex 1 0 0
vertex 1 1 0
vertex 1 1 1
endloop
endfacet
facet normal 1 0 0
outer loop
vertex 1 0 0
vertex 1 1 1
vertex 1 0 1
endloop
endfacet
facet normal 0 1 0
outer loop
vertex 1 1 0
vertex 0 1 0
vertex 0 1 1
endloop
endfacet
facet normal 0 1 0
outer loop
vertex 1 1 0
vertex 0 1 1
vertex 1 1 1
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 1 0
vertex 0 0 0
vertex 0 0 1
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 1 0
vertex 0 0 1
vertex 0 1 1
endloop
endfacet
endsolid cube
"""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
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


def _gcode_bytes(marker: str = "") -> bytes:
    """Fixture g-code plus a unique trailer so each marker hashes distinctly
    (a distinct sha256 → a distinct deduplicated Model)."""
    base = FIXTURE_GCODE.read_bytes()
    return base + f"\n; unique-marker {marker}\nG1 X0 Y0\n".encode() if marker else base


def _drop_gcode(dest_dir: Path, name: str, marker: str | None = None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    target.write_bytes(_gcode_bytes(marker or ""))
    return target


def _drop_stl(dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    target.write_bytes(_CUBE_STL)
    return target


def _stage(name: str, data: bytes) -> Path:
    staged = Path(_overlay["staging_dir"]) / "_incoming" / f"{uuid.uuid4().hex}-{name}"
    staged.write_bytes(data)
    return staged


def _external_files(session: Session, *, live_only: bool = True) -> list[File]:
    stmt = select(File).where(File.is_external == True)  # noqa: E712
    if live_only:
        stmt = stmt.where(live(File))
    session.expire_all()
    return list(session.exec(stmt).all())


# --------------------------------------------------------------------------- #
# Safety invariant: the user's NAS bytes are never destroyed
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Real folder shapes
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Revisions follow the model back into its library
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Library lifecycle via the public API
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Periodic scheduler selection
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Real folder of open-source models/g-codes
# --------------------------------------------------------------------------- #
# Defaults to the repo's ``testdata/`` folder; override with PRINTSTASH_TEST_NAS_DIR.
_REPO_TESTDATA = REPO_ROOT / "testdata"


def _real_nas_dir() -> Path | None:
    env = os.environ.get("PRINTSTASH_TEST_NAS_DIR")
    if env:
        return Path(env)
    return _REPO_TESTDATA if _REPO_TESTDATA.is_dir() else None


def _supported_files(root: Path) -> list[Path]:
    """Files under *root* the scanner recognises (mirrors ``_walk``'s filter)."""
    from app.db.models import SUFFIX_TO_FILE_TYPE

    return [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUFFIX_TO_FILE_TYPE
    ]


__all__ = [
    "ExternalLibrary",
    "ExternalLibraryCollectionMode",
    "ExternalLibraryScanStatus",
    "File",
    "FileType",
    "Model",
    "Path",
    "Session",
    "_configure_storage",
    "_drop_gcode",
    "_drop_stl",
    "_enable_feature",
    "_external_files",
    "_gcode_bytes",
    "_make_library",
    "_overlay",
    "_real_nas_dir",
    "_stage",
    "_supported_files",
    "add_gcode_revision_to_model",
    "external_library",
    "ingest_orca_gcode",
    "os",
    "pytest",
    "registry",
    "select",
    "shutil",
    "taxonomy",
    "timedelta",
    "trash",
    "utcnow",
]
