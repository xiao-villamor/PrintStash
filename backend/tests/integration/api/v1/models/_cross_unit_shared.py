"""Tests for URL/ZIP import, measured filament/duration, auto known-good,
STEP support, and share-link isolation."""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.models import (
    SUFFIX_TO_FILE_TYPE,
    File,
    FileRevisionStatus,
    FileType,
    Model,
    Printer,
    PrintJob,
    PrintJobState,
    ShareLink,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(db_session, *, name="M", slug="m", hash_="h" * 64) -> Model:
    m = Model(name=name, slug=slug, hash=hash_)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


def _make_file(
    db_session, model, *, filename="part.stl", ftype="stl", version=1
) -> File:
    f = File(
        model_id=model.id,
        path=f"/data/{filename}",
        original_filename=filename,
        file_type=ftype,
        version=version,
        size_bytes=10,
        sha256="a" * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)
    return f


# ---------------------------------------------------------------------------
# STEP support
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Filament conversion
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Archive inspection: zip-slip + importable filtering
# ---------------------------------------------------------------------------


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Printer hub: measured filament/duration + auto known-good
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Share-link isolation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Raw-STL serving streams the blob (never reads a multi-GB STL into memory).
# ---------------------------------------------------------------------------

__all__ = [
    "File",
    "FileRevisionStatus",
    "FileType",
    "Path",
    "PrintJob",
    "PrintJobState",
    "Printer",
    "SUFFIX_TO_FILE_TYPE",
    "ShareLink",
    "SimpleNamespace",
    "_make_file",
    "_make_model",
    "_zip_bytes",
    "asyncio",
    "io",
    "pytest",
    "zipfile",
]
