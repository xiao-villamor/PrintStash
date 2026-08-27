"""Defends library import rejects invalid provenance sidecar before writes at the services library transfer integration boundary.

A regression could import partial library data or lose provenance across instances.
"""

from __future__ import annotations

from ._library_transfer_shared import (
    CaptureManifestV2,
    File,
    Model,
    ModelStar,
    Path,
    PrintJob,
    PrintJobState,
    SavedView,
    Session,
    _rewrite_manifest,
    _rewrite_sidecar,
    _seed,
    json,
    library_transfer,
    provenance,
    pytest,
    select,
    utcnow,
    zipfile,
)


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
