"""Portable library archives round-trip data without unsafe or partial writes.

The suite defends archive compatibility, size/path preflight, idempotent import,
and the exact metadata operators need when moving a vault between installs.
"""

from __future__ import annotations

import inspect
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import (
    ArtifactProvenanceLink,
    File,
    FileType,
    Metadata,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelStar,
    PrintJob,
    PrintJobState,
    ProvenanceCapture,
    SavedView,
    User,
)
from app.schemas.provenance import CaptureManifestV2
from app.services import library_transfer, provenance


def _seed(db: Session, tmp_path: Path) -> tuple[User, Model, File]:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    user = db.exec(select(User)).first()
    assert user is not None
    model = Model(name="Calibration cube", slug="calibration-cube", hash="a" * 64)
    db.add(model)
    db.commit()
    db.refresh(model)
    blob = tmp_path / "files" / "calibration-cube" / "v1" / "cube.stl"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"solid cube\nendsolid cube\n")
    import hashlib

    file_row = File(
        model_id=model.id,
        path=str(blob),
        original_filename="cube.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=blob.stat().st_size,
        sha256=hashlib.sha256(blob.read_bytes()).hexdigest(),
    )
    db.add(file_row)
    db.commit()
    db.refresh(file_row)
    db.add(Metadata(file_id=file_row.id, bbox_x_mm=20, bbox_y_mm=20, bbox_z_mm=20))
    db.commit()
    return user, model, file_row


def _portable_artifact(*, source_id: int) -> dict[str, object]:
    return {
        "source_id": source_id,
        "entry": f"blobs/{source_id}.stl",
        "original_filename": f"{source_id}.stl",
        "file_type": "stl",
        "version": 1,
        "size_bytes": 0,
        "sha256": f"{source_id:x}".zfill(64),
    }


def _portable_model(*, source_id: int, artifacts: list[dict[str, object]]) -> dict:
    return {
        "source_id": source_id,
        "name": f"Model {source_id}",
        "hash": f"{source_id:x}".zfill(64),
        "artifacts": artifacts,
    }


def _rewrite_manifest(
    archive_path: Path, mutate, *, strip_provenance: bool = True
) -> Path:
    """Rewrite an archive's manifest.json via ``mutate(manifest_dict)`` in place.

    ``ZipFile`` has no in-place edit, so this reads every entry into a new
    zip, letting the caller change model identity (hash/slug) etc. without
    needing a genuinely separate target database — the identity change alone
    is what routes ``import_archive`` down the "model not found" branch.
    """
    rewritten = archive_path.with_suffix(".rewritten.zip")
    with (
        zipfile.ZipFile(archive_path) as src,
        zipfile.ZipFile(rewritten, "w", compression=zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                mutate(manifest)
                data = json.dumps(manifest).encode("utf-8")
            elif info.filename == "provenance.json" and strip_provenance:
                data = json.dumps(
                    {"format": library_transfer.PROVENANCE_FORMAT, "models": []}
                ).encode("utf-8")
            dst.writestr(info, data)
    archive_path.unlink()
    rewritten.rename(archive_path)
    return archive_path


def _rewrite_sidecar(archive_path: Path, mutate) -> Path:
    rewritten = archive_path.with_suffix(".rewritten.zip")
    with (
        zipfile.ZipFile(archive_path) as src,
        zipfile.ZipFile(rewritten, "w", compression=zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "provenance.json":
                sidecar = json.loads(data)
                mutate(sidecar)
                data = json.dumps(sidecar).encode("utf-8")
            dst.writestr(info, data)
    archive_path.unlink()
    rewritten.rename(archive_path)
    return archive_path


__all__ = [
    "ArtifactProvenanceLink",
    "CaptureManifestV2",
    "File",
    "Model",
    "ModelProvenanceField",
    "ModelProvenanceSource",
    "ModelStar",
    "Path",
    "PrintJob",
    "PrintJobState",
    "ProvenanceCapture",
    "SavedView",
    "Session",
    "TestClient",
    "_portable_artifact",
    "_portable_model",
    "_rewrite_manifest",
    "_rewrite_sidecar",
    "_seed",
    "inspect",
    "json",
    "library_transfer",
    "provenance",
    "pytest",
    "select",
    "utcnow",
    "zipfile",
]
