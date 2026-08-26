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


def test_library_archive_manifest_blobs_and_idempotent_import(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, file_row = _seed(db_session, tmp_path)
    archive_path = library_transfer.create_archive(db_session, user)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["format"] == "printstash-library-v1"
            artifact = manifest["models"][0]["artifacts"][0]
            assert artifact["sha256"] == file_row.sha256
            assert archive.read(artifact["entry"]) == Path(file_row.path).read_bytes()
        result = library_transfer.import_archive(db_session, archive_path, user)
        assert result == {
            "created_models": 0,
            "created_files": 0,
            "skipped_files": 1,
            "imported_jobs": 0,
        }
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_archive_emits_optional_prevalidated_provenance_sidecar(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, model, file_row = _seed(db_session, tmp_path)
    capture = CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://www.printables.com/model/42",
                "source_item_id": "42",
                "source_revision": "r1",
                "adapter_version": "printables-v1",
                "fields": {"title": {"value": "Captured", "origin": "confirmed"}},
            },
            "files": [
                {"id": "42:cube", "name": "cube.stl", "file_type": "stl", "size": 1}
            ],
            "selected_ids": ["42:cube"],
        }
    )
    provenance.attach_existing_artifact(
        db_session,
        file_row,
        provenance.ProvenanceContext(
            manifest=capture,
            source_file_id="42:cube",
            source_filename="cube.stl",
            blob_sha256=file_row.sha256,
        ),
        imported_overrides={"title": "Local"},
    )
    db_session.commit()

    archive_path = library_transfer.create_archive(db_session, user)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert (
                json.loads(archive.read("manifest.json"))["format"]
                == "printstash-library-v1"
            )
            sidecar = json.loads(archive.read("provenance.json"))
        assert sidecar["format"] == "printstash-provenance-v2"
        field = sidecar["models"][0]["sources"][0]["fields"][0]
        assert "overrides" not in sidecar["models"][0]["sources"][0]
        assert field == {
            "field_name": "title",
            "captured_value": "Captured",
            "captured_origin": "confirmed",
            "user_value": "Local",
            "user_override_set": True,
        }
        assert (
            sidecar["models"][0]["sources"][0]["latest_capture"]["adapter_version"]
            == "printables-v1"
        )
        assert sidecar["models"][0]["sources"][0]["latest_capture"][
            "snapshot_sha256"
        ] == provenance.snapshot_sha256(capture)
        assert (
            sidecar["models"][0]["sources"][0]["artifact_links"][0]["source_file_id"]
            == "42:cube"
        )
        assert "actor" not in json.dumps(sidecar).lower()
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_archive_round_trips_override_for_absent_captured_field(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """A sparse capture must not serialize its DB compatibility sentinel."""
    user, model, file_row = _seed(db_session, tmp_path)
    capture = CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://www.printables.com/model/absent-title",
                "source_item_id": "absent-title",
                "source_revision": None,
                "adapter_version": "printables-v1",
                "fields": {},
            },
            "files": [
                {
                    "id": "absent-title:cube",
                    "name": "cube.stl",
                    "file_type": "stl",
                    "size": file_row.size_bytes,
                }
            ],
            "selected_ids": ["absent-title:cube"],
        }
    )
    provenance.attach_existing_artifact(
        db_session,
        file_row,
        provenance.ProvenanceContext(
            manifest=capture,
            source_file_id="absent-title:cube",
            source_filename="cube.stl",
            blob_sha256=file_row.sha256,
        ),
        imported_overrides={"title": "Local title"},
    )
    db_session.commit()

    archive_path = library_transfer.create_archive(db_session, user)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            sidecar = json.loads(archive.read("provenance.json"))
        source = sidecar["models"][0]["sources"][0]
        assert source["fields"] == []
        assert source["overrides"] == [
            {"field_name": "title", "user_value": "Local title"}
        ]
        assert source["latest_capture"]["snapshot"]["fields"] == {}

        # Simulate a separate target vault in this shared database.  The
        # target still receives a strict CaptureManifestV2 parse from the
        # sidecar before any import writes.
        db_session.delete(
            db_session.exec(
                select(ArtifactProvenanceLink).where(
                    ArtifactProvenanceLink.file_id == file_row.id
                )
            ).one()
        )
        db_session.delete(
            db_session.exec(
                select(ModelProvenanceSource).where(
                    ModelProvenanceSource.model_id == model.id
                )
            ).one()
        )
        db_session.commit()
        _rewrite_manifest(
            archive_path,
            lambda manifest: manifest["models"][0].update({"hash": "e" * 64}),
            strip_provenance=False,
        )
        result = library_transfer.import_archive(db_session, archive_path, user)
        assert result["created_files"] == 1
        imported = db_session.exec(select(Model).where(Model.hash == "e" * 64)).one()
        imported_source = db_session.exec(
            select(ModelProvenanceSource).where(
                ModelProvenanceSource.model_id == imported.id
            )
        ).one()
        imported_field = db_session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == imported_source.id,
                ModelProvenanceField.field_name == "title",
            )
        ).one()
        assert provenance.effective_value(imported_field) == "Local title"
        assert (
            len(
                db_session.exec(
                    select(ProvenanceCapture).where(
                        ProvenanceCapture.provenance_source_id == imported_source.id
                    )
                ).all()
            )
            == 1
        )
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_archive_export_does_not_build_metadata_export_first(
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, _ = _seed(db_session, tmp_path)

    def _unexpected_export(*_args, **_kwargs):
        raise AssertionError("portable export must not materialize export_payload")

    monkeypatch.setattr(
        library_transfer.model_views, "export_payload", _unexpected_export
    )
    archive_path = library_transfer.create_archive(db_session, user)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.getinfo("manifest.json").file_size > 0
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_archive_export_preflights_import_entry_limit(
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    monkeypatch.setattr(library_transfer, "MAX_ENTRIES", 1)

    with pytest.raises(ValueError, match="archive_too_large"):
        library_transfer.create_archive(db_session, user)


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


def test_portable_manifest_rejects_duplicate_model_source_ids() -> None:
    payload = {
        "format": library_transfer.FORMAT,
        "models": [
            _portable_model(source_id=1, artifacts=[]),
            _portable_model(source_id=1, artifacts=[]),
        ],
    }

    with pytest.raises(ValueError, match="duplicate model source_id"):
        library_transfer.PortableManifest.model_validate(payload)


def test_portable_manifest_rejects_duplicate_artifact_source_ids() -> None:
    payload = {
        "format": library_transfer.FORMAT,
        "models": [
            _portable_model(source_id=1, artifacts=[_portable_artifact(source_id=5)]),
            _portable_model(source_id=2, artifacts=[_portable_artifact(source_id=5)]),
        ],
    }

    with pytest.raises(ValueError, match="duplicate artifact source_id"):
        library_transfer.PortableManifest.model_validate(payload)


def test_library_archive_export_rejects_oversized_manifest(
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    monkeypatch.setattr(library_transfer, "MAX_MANIFEST_BYTES", 1)

    with pytest.raises(ValueError, match="archive_too_large"):
        library_transfer.create_archive(db_session, user)


def test_artifact_member_validation_rejects_missing_blob(tmp_path: Path) -> None:
    archive_path = tmp_path / "missing-blob.zip"
    with zipfile.ZipFile(archive_path, "w"):
        pass

    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError, match="archive_blob_missing"):
            library_transfer._validate_artifact_member(
                archive,
                {"entry": "blobs/missing.stl", "size_bytes": 0, "sha256": "a" * 64},
            )


def test_artifact_member_validation_rejects_non_numeric_size(tmp_path: Path) -> None:
    archive_path = tmp_path / "invalid-size.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("blobs/part.stl", b"solid")

    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError, match="invalid_manifest"):
            library_transfer._validate_artifact_member(
                archive,
                {
                    "entry": "blobs/part.stl",
                    "size_bytes": "not-a-number",
                    "sha256": "a" * 64,
                },
            )


def test_sidecar_reader_rejects_duplicate_manifest_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate-manifest.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("manifest.json", "{}")

    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError, match="portable_manifest_invalid"):
            library_transfer._read_provenance_sidecar(archive, {"models": []})


def test_sidecar_reader_accepts_archive_without_optional_sidecar(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "without-sidecar.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", "{}")

    with zipfile.ZipFile(archive_path) as archive:
        result = library_transfer._read_provenance_sidecar(archive, {"models": []})

    assert result is None


def test_library_archive_limit_covers_large_library_reference() -> None:
    assert library_transfer.MAX_ENTRIES >= 25_001


def test_library_archive_export_rejects_changed_source_blob(
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    user, _, file_row = _seed(db_session, tmp_path)
    source = Path(file_row.path)
    source.write_bytes(b"x" * file_row.size_bytes)

    with pytest.raises(ValueError, match="archive_blob_hash_mismatch"):
        library_transfer.create_archive(db_session, user)


def test_library_import_hashes_artifact_members_without_zipfile_read(
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    archive_path = library_transfer.create_archive(db_session, user)
    original_read = zipfile.ZipFile.read

    def _guarded_read(self, name, *args, **kwargs):
        filename = name.filename if isinstance(name, zipfile.ZipInfo) else str(name)
        if filename.startswith("blobs/"):
            raise AssertionError("Artifact validation must stream ZIP members")
        return original_read(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", _guarded_read)
    try:
        result = library_transfer.import_archive(db_session, archive_path, user)
        assert result["skipped_files"] == 1
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_import_api_runs_blocking_work_in_fastapi_threadpool() -> None:
    from app.api.v1 import models as models_api

    assert not inspect.iscoroutinefunction(models_api.import_library_archive)


def test_library_import_rejects_corrupt_blob(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    source = library_transfer.create_archive(db_session, user)
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(corrupt, "w") as output:
        for info in original.infolist():
            data = original.read(info.filename)
            output.writestr(
                info, b"corrupt" if info.filename.startswith("blobs/") else data
            )
    with pytest.raises(ValueError, match="archive_blob_hash_mismatch"):
        library_transfer.import_archive(db_session, corrupt, user)
    source.unlink(missing_ok=True)


def test_library_archive_api_downloads_zip(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    _seed(db_session, tmp_path)
    response = client.get("/api/v1/models/library-archive", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    downloaded = tmp_path / "download.zip"
    downloaded.write_bytes(response.content)
    with zipfile.ZipFile(downloaded) as archive:
        assert (
            json.loads(archive.read("manifest.json"))["format"]
            == "printstash-library-v1"
        )


@pytest.mark.parametrize(
    ("detail", "status_code"),
    [("archive_too_large", 413), ("archive_blob_hash_mismatch", 409)],
)
def test_library_archive_api_reports_preflight_failure(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    detail: str,
    status_code: int,
) -> None:
    def _reject_export(*_args, **_kwargs):
        raise ValueError(detail)

    monkeypatch.setattr(library_transfer, "create_archive", _reject_export)
    response = client.get("/api/v1/models/library-archive", headers=auth_headers)

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


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


def test_library_import_restores_provenance_into_new_model(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, model, file_row = _seed(db_session, tmp_path)
    capture = CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://printables.com/model/portable-roundtrip-42",
                "source_item_id": "portable-roundtrip-42",
                "source_revision": "r1",
                "adapter_version": "adapter-1",
                "fields": {"title": {"value": "Captured", "origin": "confirmed"}},
            },
            "files": [
                {
                    "id": "portable-roundtrip-42:cube",
                    "name": "cube.stl",
                    "file_type": "stl",
                    "size": file_row.size_bytes,
                }
            ],
            "selected_ids": ["portable-roundtrip-42:cube"],
        }
    )
    provenance.attach_existing_artifact(
        db_session,
        file_row,
        provenance.ProvenanceContext(
            manifest=capture,
            source_file_id="portable-roundtrip-42:cube",
            source_filename="cube.stl",
            blob_sha256=file_row.sha256,
        ),
        imported_overrides={"title": "Local"},
    )
    db_session.commit()
    archive_path = library_transfer.create_archive(db_session, user)
    try:
        # The archive is imported into an independent Vault. Remove the
        # source-side link from this one-process fixture before exercising the
        # target path; a real second Vault has no global import-key collision.
        db_session.delete(
            db_session.exec(
                select(ArtifactProvenanceLink).where(
                    ArtifactProvenanceLink.file_id == file_row.id
                )
            ).one()
        )
        db_session.delete(
            db_session.exec(
                select(ModelProvenanceSource).where(
                    ModelProvenanceSource.model_id == model.id
                )
            ).one()
        )
        db_session.commit()
        _rewrite_manifest(
            archive_path,
            lambda manifest: manifest["models"][0].update({"hash": "f" * 64}),
            strip_provenance=False,
        )
        result = library_transfer.import_archive(db_session, archive_path, user)
        assert result["created_files"] == 1
        imported = db_session.exec(select(Model).where(Model.hash == "f" * 64)).one()
        source = db_session.exec(
            select(ModelProvenanceSource).where(
                ModelProvenanceSource.model_id == imported.id
            )
        ).one()
        assert source.provider == "printables"
        field = db_session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == source.id
            )
        ).one()
        assert json.loads(field.user_value_json) == "Local"
    finally:
        archive_path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda sidecar: sidecar["models"][0]["sources"][0]["artifact_links"][0].update(
            {"artifact_source_id": 999}
        ),
        lambda sidecar: sidecar["models"][0]["sources"][0]["artifact_links"][0].update(
            {"blob_sha256": "0" * 64}
        ),
        lambda sidecar: sidecar["models"][0]["sources"][0]["artifact_links"][0].update(
            {"source_filename": "../escape.stl"}
        ),
    ],
)
def test_library_import_rejects_invalid_provenance_sidecar_before_writes(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path, mutate
) -> None:
    user, _model, file_row = _seed(db_session, tmp_path)
    capture = CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://printables.com/model/42",
                "source_item_id": "42",
                "source_revision": None,
                "adapter_version": "adapter-1",
                "fields": {},
            },
            "files": [
                {
                    "id": "42:cube",
                    "name": "cube.stl",
                    "file_type": "stl",
                    "size": file_row.size_bytes,
                }
            ],
            "selected_ids": ["42:cube"],
        }
    )
    provenance.attach_existing_artifact(
        db_session,
        file_row,
        provenance.ProvenanceContext(
            manifest=capture,
            source_file_id="42:cube",
            source_filename="cube.stl",
            blob_sha256=file_row.sha256,
        ),
    )
    db_session.commit()
    archive_path = library_transfer.create_archive(db_session, user)
    try:
        _rewrite_sidecar(archive_path, mutate)
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer.import_archive(db_session, archive_path, user)
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_import_rejects_empty_captured_sidecar_value(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _model, file_row = _seed(db_session, tmp_path)
    capture = CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://printables.com/model/strict-captured",
                "source_item_id": "strict-captured",
                "source_revision": None,
                "adapter_version": "adapter-1",
                "fields": {"title": {"value": "Captured", "origin": "confirmed"}},
            },
            "files": [
                {
                    "id": "strict-captured:cube",
                    "name": "cube.stl",
                    "file_type": "stl",
                    "size": file_row.size_bytes,
                }
            ],
            "selected_ids": ["strict-captured:cube"],
        }
    )
    provenance.attach_existing_artifact(
        db_session,
        file_row,
        provenance.ProvenanceContext(
            manifest=capture,
            source_file_id="strict-captured:cube",
            source_filename="cube.stl",
            blob_sha256=file_row.sha256,
        ),
    )
    db_session.commit()
    archive_path = library_transfer.create_archive(db_session, user)
    try:
        _rewrite_sidecar(
            archive_path,
            lambda sidecar: sidecar["models"][0]["sources"][0]["fields"][0].update(
                {"captured_value": ""}
            ),
        )
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer.import_archive(db_session, archive_path, user)
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_import_creates_new_model_star_and_print_job(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """A manifest hash the target DB has never seen: the 'model not found'
    branch creates a new model/file, and the starred flag + a print job
    carry over with it."""
    user, model, file_row = _seed(db_session, tmp_path)
    db_session.add(ModelStar(user_id=user.id, model_id=model.id))
    db_session.add(
        PrintJob(
            model_id=model.id,
            file_id=file_row.id,
            remote_filename="cube.gcode",
            state=PrintJobState.COMPLETED,
            source="manual",
            started_at=utcnow(),
            finished_at=utcnow(),
        )
    )
    db_session.commit()

    archive_path = library_transfer.create_archive(db_session, user)
    try:
        # A hash/slug the target DB has never seen — models.hash and
        # models.slug are unique DB-wide, so a real "fresh target" is always
        # a separate vault; this simulates that without a second database.
        def _new_identity(manifest: dict) -> None:
            manifest["models"][0]["hash"] = "f" * 64
            manifest["models"][0]["slug"] = "calibration-cube-imported"

        _rewrite_manifest(archive_path, _new_identity)

        result = library_transfer.import_archive(db_session, archive_path, user)
        assert result["created_models"] == 1
        assert result["created_files"] == 1
        assert result["skipped_files"] == 0
        assert result["imported_jobs"] == 1

        new_model = db_session.exec(select(Model).where(Model.hash == "f" * 64)).one()
        assert (
            db_session.exec(
                select(ModelStar).where(
                    ModelStar.user_id == user.id, ModelStar.model_id == new_model.id
                )
            ).first()
            is not None
        )
        assert (
            db_session.exec(
                select(PrintJob).where(PrintJob.model_id == new_model.id)
            ).first()
            is not None
        )

        # Re-importing the same (rewritten) archive must not duplicate the job.
        result2 = library_transfer.import_archive(db_session, archive_path, user)
        assert result2["imported_jobs"] == 0
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_import_creates_collection_from_manifest_path(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, model, _file_row = _seed(db_session, tmp_path)
    archive_path = library_transfer.create_archive(db_session, user)
    try:

        def _new_identity_with_collection(manifest: dict) -> None:
            manifest["models"][0]["hash"] = "e" * 64
            manifest["models"][0]["slug"] = "calibration-cube-vases"
            manifest["models"][0]["collection"] = "Vases/Tall"

        _rewrite_manifest(archive_path, _new_identity_with_collection)

        library_transfer.import_archive(db_session, archive_path, user)

        from app.db.models import Collection

        new_model = db_session.exec(select(Model).where(Model.hash == "e" * 64)).one()
        assert new_model.collection_id is not None
        new_collection = db_session.get(Collection, new_model.collection_id)
        # Collection paths are slugified on resolve-or-create, not preserved verbatim.
        assert new_collection is not None and new_collection.path == "vases/tall"
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_import_skips_existing_saved_view_by_name(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _model, _file_row = _seed(db_session, tmp_path)
    db_session.add(SavedView(user_id=user.id, name="My View", filters_json="{}"))
    db_session.commit()

    archive_path = library_transfer.create_archive(db_session, user)
    try:
        library_transfer.import_archive(db_session, archive_path, user)
        views = db_session.exec(
            select(SavedView).where(
                SavedView.user_id == user.id, SavedView.name == "My View"
            )
        ).all()
        assert len(views) == 1  # not duplicated
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_import_rejects_archive_too_large(
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    archive_path = library_transfer.create_archive(db_session, user)
    try:
        monkeypatch.setattr(library_transfer, "MAX_ENTRIES", 1)
        with pytest.raises(ValueError, match="archive_too_large"):
            library_transfer.import_archive(db_session, archive_path, user)
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_import_rejects_unsafe_archive_path(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    malicious = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"format": library_transfer.FORMAT, "models": []}),
        )
        archive.writestr("../../etc/evil.txt", b"pwned")

    with pytest.raises(ValueError, match="unsafe_archive_path"):
        library_transfer.import_archive(db_session, malicious, user)


def test_library_import_ignores_absolute_manifest_slug(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    archive_path = library_transfer.create_archive(db_session, user)
    escaped = tmp_path / "outside-vault"
    try:

        def _absolute_slug(manifest: dict) -> None:
            manifest["models"][0]["hash"] = "d" * 64
            manifest["models"][0]["slug"] = str(escaped)

        _rewrite_manifest(archive_path, _absolute_slug)
        result = library_transfer.import_archive(db_session, archive_path, user)

        assert result["created_files"] == 1
        assert not escaped.exists()
        imported = db_session.exec(select(Model).where(Model.hash == "d" * 64)).one()
        assert imported.slug != str(escaped)
        artifact = db_session.exec(
            select(File).where(File.model_id == imported.id)
        ).one()
        assert Path(artifact.path).is_relative_to(tmp_path / "files")
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_import_rejects_absolute_original_filename_before_writes(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    archive_path = library_transfer.create_archive(db_session, user)
    escaped = tmp_path / "outside.stl"
    try:

        def _absolute_filename(manifest: dict) -> None:
            manifest["models"][0]["hash"] = "c" * 64
            manifest["models"][0]["artifacts"][0]["original_filename"] = str(escaped)

        _rewrite_manifest(archive_path, _absolute_filename)
        with pytest.raises(ValueError, match="portable_manifest_invalid"):
            library_transfer.import_archive(db_session, archive_path, user)

        assert not escaped.exists()
        assert (
            db_session.exec(select(Model).where(Model.hash == "c" * 64)).first() is None
        )
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_import_caps_manifest_before_materializing_it(
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    archive_path = tmp_path / "large-manifest.zip"
    manifest = json.dumps(
        {"format": library_transfer.FORMAT, "models": [], "padding": "x" * 4096}
    )
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("manifest.json", manifest)
    monkeypatch.setattr(library_transfer, "MAX_MANIFEST_BYTES", 1024)

    with pytest.raises(ValueError, match="portable_manifest_invalid"):
        library_transfer.import_archive(db_session, archive_path, user)


def test_library_import_rejects_missing_manifest(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    no_manifest = tmp_path / "no-manifest.zip"
    with zipfile.ZipFile(no_manifest, "w") as archive:
        archive.writestr("readme.txt", b"nothing to see here")

    with pytest.raises(ValueError, match="portable_manifest_invalid"):
        library_transfer.import_archive(db_session, no_manifest, user)


def test_library_import_rejects_wrong_format(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    wrong_format = tmp_path / "wrong-format.zip"
    with zipfile.ZipFile(wrong_format, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"format": "some-other-format-v9", "models": []}),
        )

    with pytest.raises(ValueError, match="portable_manifest_invalid"):
        library_transfer.import_archive(db_session, wrong_format, user)
