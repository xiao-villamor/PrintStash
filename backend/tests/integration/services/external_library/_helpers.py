"""Shared arrange helpers for the external-library (NAS mirroring) suite.

Every file in this folder needs the same three things: the feature flag on (it
is off by default, and a scan silently does nothing while it is), a file on a
fake NAS folder whose bytes hash distinctly from every other test's, and a way
to read the index rows back. Duplicating those in each file is how a suite ends
up with six subtly different "drop a g-code" helpers, so they live here.

`gcode_bytes` takes a marker for a reason: ingestion deduplicates by sha256, so
two tests that both drop the unmodified fixture end up sharing one Model, and an
assertion about "the model this test created" silently reads the other test's
row. A distinct marker means a distinct hash means a distinct Model.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import File
from app.db.scopes import live
from app.services import runtime_config
from tests.paths import FIXTURES_DIR

FIXTURE_GCODE = FIXTURES_DIR / "sample.gcode"

# A small but valid ASCII-STL cube: a real mesh trimesh can parse and
# thumbnail, so a scan that indexes it exercises the mesh strategy rather
# than a stub that would never reach it.
CUBE_STL = b"""solid cube
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


def enable_feature(session: Session) -> None:
    runtime_config.set_external_libraries_enabled(session, True)


def gcode_bytes(marker: str = "") -> bytes:
    """Fixture g-code plus a unique trailer so each marker hashes distinctly
    (a distinct sha256 → a distinct deduplicated Model)."""
    base = FIXTURE_GCODE.read_bytes()
    return base + f"\n; unique-marker {marker}\nG1 X0 Y0\n".encode() if marker else base


def drop_gcode(dest_dir: Path, name: str, marker: str | None = None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    target.write_bytes(gcode_bytes(marker or ""))
    return target


def drop_stl(dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    target.write_bytes(CUBE_STL)
    return target


def stage(name: str, data: bytes) -> Path:
    staged = Path(_overlay["staging_dir"]) / "_incoming" / f"{uuid.uuid4().hex}-{name}"
    staged.write_bytes(data)
    return staged


def external_files(session: Session, *, live_only: bool = True) -> list[File]:
    stmt = select(File).where(File.is_external == True)  # noqa: E712
    if live_only:
        stmt = stmt.where(live(File))
    session.expire_all()
    return list(session.exec(stmt).all())
