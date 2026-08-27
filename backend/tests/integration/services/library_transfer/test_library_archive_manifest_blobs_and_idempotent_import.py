"""Defends library archive manifest blobs and idempotent import at the services library transfer integration boundary.

A regression could import partial library data or lose provenance across instances.
"""

from __future__ import annotations

import hashlib

from app.db.models import File, FileType

from ._library_transfer_shared import (
    ArtifactProvenanceLink,
    CaptureManifestV2,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    Path,
    ProvenanceCapture,
    Session,
    TestClient,
    _portable_artifact,
    _portable_model,
    _rewrite_manifest,
    _seed,
    inspect,
    json,
    library_transfer,
    provenance,
    pytest,
    select,
    zipfile,
)


def test_library_archive_contains_versioned_manifest_and_owned_blob(
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
    finally:
        archive_path.unlink(missing_ok=True)


def test_library_archive_reimport_skips_existing_artifact_without_mutation(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, _, _ = _seed(db_session, tmp_path)
    archive_path = library_transfer.create_archive(db_session, user)

    try:
        result = library_transfer.import_archive(db_session, archive_path, user)
    finally:
        archive_path.unlink(missing_ok=True)

    assert result == {
        "created_models": 0,
        "created_files": 0,
        "skipped_files": 1,
        "imported_jobs": 0,
    }


def test_library_archive_manifest_preserves_revision_hashes_and_recommendation(
    db_session: Session, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    user, model, first = _seed(db_session, tmp_path)
    revision_bytes = b"G28\n; second revision\n"
    revision_path = tmp_path / "files" / model.slug / "v2" / "revision.gcode"
    revision_path.parent.mkdir(parents=True)
    revision_path.write_bytes(revision_bytes)
    revision = File(
        model_id=model.id,
        path=str(revision_path),
        original_filename="revision.gcode",
        file_type=FileType.GCODE,
        version=2,
        size_bytes=len(revision_bytes),
        sha256=hashlib.sha256(revision_bytes).hexdigest(),
        revision_label="Second slice",
        is_recommended=True,
    )
    db_session.add(revision)
    db_session.commit()
    archive_path = library_transfer.create_archive(db_session, user)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            artifacts = json.loads(archive.read("manifest.json"))["models"][0][
                "artifacts"
            ]
    finally:
        archive_path.unlink(missing_ok=True)

    assert [(row["version"], row["sha256"]) for row in artifacts] == [
        (1, first.sha256),
        (2, revision.sha256),
    ]
    assert [row["is_recommended"] for row in artifacts] == [False, True]
    assert artifacts[1]["revision_label"] == "Second slice"


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
