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
    File,
    FileType,
    Metadata,
    Model,
    ModelStar,
    PrintJob,
    PrintJobState,
    SavedView,
    User,
)
from app.services import library_transfer


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
        assert json.loads(archive.read("manifest.json"))["format"] == "printstash-library-v1"


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


def _rewrite_manifest(archive_path: Path, mutate) -> Path:
    """Rewrite an archive's manifest.json via ``mutate(manifest_dict)`` in place.

    ``ZipFile`` has no in-place edit, so this reads every entry into a new
    zip, letting the caller change model identity (hash/slug) etc. without
    needing a genuinely separate target database — the identity change alone
    is what routes ``import_archive`` down the "model not found" branch.
    """
    rewritten = archive_path.with_suffix(".rewritten.zip")
    with zipfile.ZipFile(archive_path) as src, zipfile.ZipFile(
        rewritten, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                mutate(manifest)
                data = json.dumps(manifest).encode("utf-8")
            dst.writestr(info, data)
    archive_path.unlink()
    rewritten.rename(archive_path)
    return archive_path


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

        new_model = db_session.exec(
            select(Model).where(Model.hash == "f" * 64)
        ).one()
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
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        archive.writestr("manifest.json", json.dumps({"format": library_transfer.FORMAT, "models": []}))
        archive.writestr("../../etc/evil.txt", b"pwned")

    with pytest.raises(ValueError, match="unsafe_archive_path"):
        library_transfer.import_archive(db_session, malicious, user)


def test_library_import_rejects_missing_manifest(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    no_manifest = tmp_path / "no-manifest.zip"
    with zipfile.ZipFile(no_manifest, "w") as archive:
        archive.writestr("readme.txt", b"nothing to see here")

    with pytest.raises(ValueError, match="invalid_manifest"):
        library_transfer.import_archive(db_session, no_manifest, user)


def test_library_import_rejects_wrong_format(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    wrong_format = tmp_path / "wrong-format.zip"
    with zipfile.ZipFile(wrong_format, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "some-other-format-v9", "models": []}))

    with pytest.raises(ValueError, match="unsupported_archive_format"):
        library_transfer.import_archive(db_session, wrong_format, user)
