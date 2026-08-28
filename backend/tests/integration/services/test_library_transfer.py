"""Moving a library between instances without trusting the archive that carries it.

A portable export is a file a user hands to another machine — a backup they are
restoring, a library they are migrating, or an archive somebody sent them. On
import it is **untrusted input that names filesystem paths**, which makes it the
most dangerous shape of data this application accepts: an entry called
`/etc/cron.d/x` or `../../secrets` is a write outside the vault if anything
along the path is taken at face value.

So the import side is written as a series of refusals, and this file is mostly
those refusals. Every one is checked *before the first byte is written*, because
a half-applied import is worse than a rejected one: it leaves rows pointing at
files that were never created and files nothing owns.

The manifest is versioned, and both versions are covered, because a self-hoster
restores an archive produced by an older release. Dropping v1 support silently
turns their backup into an unreadable file.

The cover and sidecar rows are about the same principle applied to metadata:
bytes are only accepted when they match the hash the manifest declared for them,
so a tampered archive cannot swap an image for something else.
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
from tests.factories import build_file, build_model, build_print_job


def _seed(db: Session, tmp_path: Path) -> tuple[User, Model, File]:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    user = db.exec(select(User)).first()
    assert user is not None
    model = build_model(
        db, name="Calibration cube", slug="calibration-cube", hash="a" * 64
    )
    db.refresh(model)
    blob = tmp_path / "files" / "calibration-cube" / "v1" / "cube.stl"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"solid cube\nendsolid cube\n")
    import hashlib

    file_row = build_file(
        db,
        model,
        path=str(blob),
        filename="cube.stl",
        file_type=FileType.STL,
        size_bytes=blob.stat().st_size,
        sha256=hashlib.sha256(blob.read_bytes()).hexdigest(),
    )
    db.refresh(file_row)
    db.add(Metadata(file_id=file_row.id, bbox_x_mm=20, bbox_y_mm=20, bbox_z_mm=20))
    db.commit()
    return user, model, file_row


class TestImportArchive:
    def test_library_import_api_runs_blocking_work_in_fastapi_threadpool(self) -> None:
        from app.api.v1 import models as models_api

        assert not inspect.iscoroutinefunction(models_api.import_library_archive)

    def test_library_import_rejects_corrupt_blob(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        user, _, _ = _seed(db_session, tmp_path)
        source = library_transfer.create_archive(db_session, user)
        corrupt = tmp_path / "corrupt.zip"
        with (
            zipfile.ZipFile(source) as original,
            zipfile.ZipFile(corrupt, "w") as output,
        ):
            for info in original.infolist():
                data = original.read(info.filename)
                output.writestr(
                    info, b"corrupt" if info.filename.startswith("blobs/") else data
                )
        with pytest.raises(ValueError, match="archive_blob_hash_mismatch"):
            library_transfer.import_archive(db_session, corrupt, user)
        source.unlink(missing_ok=True)

    def test_library_import_skips_existing_saved_view_by_name(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
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

    def test_library_import_rejects_absolute_original_filename_before_writes(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        user, _, _ = _seed(db_session, tmp_path)
        archive_path = library_transfer.create_archive(db_session, user)
        escaped = tmp_path / "outside.stl"
        try:

            def _absolute_filename(manifest: dict) -> None:
                manifest["models"][0]["hash"] = "c" * 64
                manifest["models"][0]["artifacts"][0]["original_filename"] = str(
                    escaped
                )

            _rewrite_manifest(archive_path, _absolute_filename)
            with pytest.raises(ValueError, match="portable_manifest_invalid"):
                library_transfer.import_archive(db_session, archive_path, user)

            assert not escaped.exists()
            assert (
                db_session.exec(select(Model).where(Model.hash == "c" * 64)).first()
                is None
            )
        finally:
            archive_path.unlink(missing_ok=True)

    def test_library_import_rejects_wrong_format(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
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

    def test_a_library_import_recreates_everything_attached_to_the_model(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        """A manifest hash the target DB has never seen: the 'model not found'
        branch creates a new model/file, and the starred flag + a print job
        carry over with it."""
        user, model, file_row = _seed(db_session, tmp_path)
        db_session.add(ModelStar(user_id=user.id, model_id=model.id))
        build_print_job(
            db_session,
            file_row,
            remote_filename="cube.gcode",
            state=PrintJobState.COMPLETED,
            source="manual",
            started_at=utcnow(),
            finished_at=utcnow(),
        )

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
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
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

            new_model = db_session.exec(
                select(Model).where(Model.hash == "e" * 64)
            ).one()
            assert new_model.collection_id is not None
            new_collection = db_session.get(Collection, new_model.collection_id)
            # Collection paths are slugified on resolve-or-create, not preserved verbatim.
            assert new_collection is not None and new_collection.path == "vases/tall"
        finally:
            archive_path.unlink(missing_ok=True)

    def test_library_import_hashes_artifact_members_without_zipfile_read(
        self,
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

    def test_library_import_rejects_empty_captured_sidecar_value(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
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

    def test_re_importing_the_same_archive_changes_nothing(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        user, _, file_row = _seed(db_session, tmp_path)
        archive_path = library_transfer.create_archive(db_session, user)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["format"] == "printstash-library-v1"
                artifact = manifest["models"][0]["artifacts"][0]
                assert artifact["sha256"] == file_row.sha256
                assert (
                    archive.read(artifact["entry"]) == Path(file_row.path).read_bytes()
                )
            result = library_transfer.import_archive(db_session, archive_path, user)
            assert result == {
                "created_models": 0,
                "created_files": 0,
                "skipped_files": 1,
                "imported_jobs": 0,
            }
        finally:
            archive_path.unlink(missing_ok=True)

    def test_library_import_ignores_absolute_manifest_slug(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
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
            imported = db_session.exec(
                select(Model).where(Model.hash == "d" * 64)
            ).one()
            assert imported.slug != str(escaped)
            artifact = db_session.exec(
                select(File).where(File.model_id == imported.id)
            ).one()
            assert Path(artifact.path).is_relative_to(tmp_path / "files")
        finally:
            archive_path.unlink(missing_ok=True)

    def test_library_import_caps_manifest_before_materializing_it(
        self,
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
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
    ) -> None:
        user, _, _ = _seed(db_session, tmp_path)
        no_manifest = tmp_path / "no-manifest.zip"
        with zipfile.ZipFile(no_manifest, "w") as archive:
            archive.writestr("readme.txt", b"nothing to see here")

        with pytest.raises(ValueError, match="portable_manifest_invalid"):
            library_transfer.import_archive(db_session, no_manifest, user)

    def test_library_import_rejects_archive_too_large(
        self,
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
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
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

    def test_library_import_restores_provenance_into_new_model(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
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
            imported = db_session.exec(
                select(Model).where(Model.hash == "f" * 64)
            ).one()
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
            lambda sidecar: sidecar["models"][0]["sources"][0]["artifact_links"][
                0
            ].update({"artifact_source_id": 999}),
            lambda sidecar: sidecar["models"][0]["sources"][0]["artifact_links"][
                0
            ].update({"blob_sha256": "0" * 64}),
            lambda sidecar: sidecar["models"][0]["sources"][0]["artifact_links"][
                0
            ].update({"source_filename": "../escape.stl"}),
        ],
    )
    def test_library_import_rejects_invalid_provenance_sidecar_before_writes(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path, mutate
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


class TestValidateProvenanceCoverMembers:
    """Preflighting the cover images in a portable archive before any writes.

    A portable archive is a zip from a stranger's machine, and its provenance sidecar
    *names* members of that zip. Every one of those names is untrusted: it can point
    outside the archive, at a directory, at a member that does not exist, at one that
    exists twice, or at bytes that do not match the hash the sidecar claims. The whole
    check runs **before** a single row or file is written, because a partially imported
    library with half its covers wrong is worse than a refused import.
    """

    @staticmethod
    def _archive(tmp_path: Path, members: dict[str, bytes]) -> zipfile.ZipFile:
        path = tmp_path / "portable.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            for name, data in members.items():
                bundle.writestr(name, data)
        return zipfile.ZipFile(path)

    @staticmethod
    def _sidecar(cover: object) -> dict:
        return {"models": [{"sources": [{"cover": cover}]}]}

    @staticmethod
    def _cover(data: bytes, entry: str = "covers/1.webp") -> dict:
        import hashlib as _hashlib

        return {
            "entry": entry,
            "content_type": "image/webp",
            "size_bytes": len(data),
            "sha256": _hashlib.sha256(data).hexdigest(),
        }

    def test_accepts_a_cover_whose_bytes_match_its_hash(self, tmp_path: Path) -> None:
        data = b"webp-bytes"
        archive = self._archive(tmp_path, {"covers/1.webp": data})

        library_transfer._validate_provenance_cover_members(
            archive, self._sidecar(self._cover(data))
        )

    def test_accepts_an_archive_with_no_sidecar(self, tmp_path: Path) -> None:
        archive = self._archive(tmp_path, {"manifest.json": b"{}"})

        library_transfer._validate_provenance_cover_members(archive, None)

    def test_accepts_a_source_that_declares_no_cover(self, tmp_path: Path) -> None:
        archive = self._archive(tmp_path, {"manifest.json": b"{}"})

        library_transfer._validate_provenance_cover_members(
            archive, {"models": [{"sources": [{}]}]}
        )

    @pytest.mark.parametrize(
        "sidecar",
        [
            pytest.param({"models": ["not-a-dict"]}, id="model-not-a-dict"),
            pytest.param(
                {"models": [{"sources": "not-a-list"}]}, id="sources-not-a-list"
            ),
        ],
    )
    def test_refuses_a_sidecar_that_is_not_the_right_shape(
        self, tmp_path: Path, sidecar: dict
    ) -> None:
        archive = self._archive(tmp_path, {"manifest.json": b"{}"})

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(archive, sidecar)

    @pytest.mark.parametrize(
        ("mutation", "identifier"),
        [
            pytest.param({"entry": "../escape.webp"}, "path-traversal", id="traversal"),
            pytest.param({"entry": "/absolute.webp"}, "absolute", id="absolute-path"),
            pytest.param({"entry": "elsewhere/1.webp"}, "outside", id="outside-covers"),
            pytest.param(
                {"content_type": "image/png"}, "type", id="wrong-content-type"
            ),
            pytest.param({"size_bytes": -1}, "negative", id="negative-size"),
            pytest.param({"size_bytes": True}, "bool", id="bool-size"),
            pytest.param({"size_bytes": "10"}, "string", id="string-size"),
            pytest.param({"sha256": "not-a-hash"}, "hash", id="malformed-hash"),
        ],
    )
    def test_refuses_a_cover_field_it_does_not_trust(
        self, tmp_path: Path, mutation: dict, identifier: str
    ) -> None:
        data = b"webp-bytes"
        archive = self._archive(tmp_path, {"covers/1.webp": data})
        cover = {**self._cover(data), **mutation}

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                archive, self._sidecar(cover)
            )

    def test_refuses_a_cover_that_is_not_an_object(self, tmp_path: Path) -> None:
        archive = self._archive(tmp_path, {"covers/1.webp": b"webp-bytes"})

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                archive, self._sidecar("not-a-dict")
            )

    def test_refuses_a_cover_carrying_a_field_it_does_not_know(
        self, tmp_path: Path
    ) -> None:
        data = b"webp-bytes"
        archive = self._archive(tmp_path, {"covers/1.webp": data})
        cover = {**self._cover(data), "surprise": 1}

        # An exact key set, not a superset: an unknown field is a manifest from a
        # newer or forged writer and is not safe to guess at.
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                archive, self._sidecar(cover)
            )

    def test_refuses_a_cover_larger_than_the_cap(self, tmp_path: Path) -> None:
        data = b"webp-bytes"
        archive = self._archive(tmp_path, {"covers/1.webp": data})
        cover = {
            **self._cover(data),
            "size_bytes": library_transfer._MAX_COVER_BYTES + 1,
        }

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                archive, self._sidecar(cover)
            )

    def test_refuses_a_member_the_archive_does_not_contain(
        self, tmp_path: Path
    ) -> None:
        data = b"webp-bytes"
        archive = self._archive(tmp_path, {"manifest.json": b"{}"})

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                archive, self._sidecar(self._cover(data))
            )

    def test_refuses_the_same_member_claimed_twice(self, tmp_path: Path) -> None:
        data = b"webp-bytes"
        archive = self._archive(tmp_path, {"covers/1.webp": data})
        cover = self._cover(data)

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                archive,
                {"models": [{"sources": [{"cover": cover}, {"cover": dict(cover)}]}]},
            )

    def test_refuses_a_member_that_appears_twice_in_the_archive(
        self, tmp_path: Path
    ) -> None:
        data = b"webp-bytes"
        path = tmp_path / "duplicated.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("covers/1.webp", data)
            bundle.writestr("covers/1.webp", data)

        # A zip may hold two members with one name; a reader picking either is a
        # place to smuggle different bytes past the hash check.
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                zipfile.ZipFile(path), self._sidecar(self._cover(data))
            )

    def test_refuses_a_member_whose_declared_size_is_wrong(
        self, tmp_path: Path
    ) -> None:
        data = b"webp-bytes"
        archive = self._archive(tmp_path, {"covers/1.webp": data})
        cover = {**self._cover(data), "size_bytes": len(data) + 5}

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                archive, self._sidecar(cover)
            )

    def test_refuses_a_member_whose_bytes_do_not_match_the_hash(
        self, tmp_path: Path
    ) -> None:
        archive = self._archive(tmp_path, {"covers/1.webp": b"different"})
        cover = self._cover(b"webp-bytes")
        cover["size_bytes"] = len(b"different")

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                archive, self._sidecar(cover)
            )

    def test_refuses_a_directory_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "dir.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("covers/1.webp/", b"")

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            library_transfer._validate_provenance_cover_members(
                zipfile.ZipFile(path),
                self._sidecar(
                    {
                        "entry": "covers/1.webp/",
                        "content_type": "image/webp",
                        "size_bytes": 0,
                        "sha256": "0" * 64,
                    }
                ),
            )


class TestPortableProvenanceContextsV1:
    """Reading a *legacy* provenance sidecar out of a portable archive.

    PrintStash writes the v2 format, but an archive exported by an older install still
    has to import, so this branch keeps reading v1. Everything in it comes from another
    machine, so the transformation is strict in a way a same-version reader would not
    need to be: an unknown key is a refusal rather than something to ignore, a link must
    name an artifact the manifest actually contains, and its `blob_sha256` must match
    that artifact's — otherwise the archive could attach one model's provenance to
    another's bytes.

    It is also deliberately **preflight-only**: it returns contexts and writes nothing,
    so a refusal partway through leaves no half-restored provenance behind.
    """

    ARTIFACT = {
        "source_id": 11,
        "sha256": "a" * 64,
        "file_type": "stl",
        "size_bytes": 1,
    }

    @classmethod
    def _manifest(cls) -> dict:
        return {"models": [{"source_id": 7, "artifacts": [dict(cls.ARTIFACT)]}]}

    @classmethod
    def _link(cls, **overrides) -> dict:
        return {
            "artifact_source_id": 11,
            "source_file_id": "42:cube",
            "source_filename": "cube.stl",
            "container_entry_path": None,
            "source_revision": None,
            "blob_sha256": cls.ARTIFACT["sha256"],
            **overrides,
        }

    @classmethod
    def _source(cls, **overrides) -> dict:
        return {
            "source_id": 3,
            "provider": "printables",
            "canonical_url": "https://www.printables.com/model/42",
            "source_item_id": "42",
            "source_revision": None,
            "fields": [
                {
                    "field_name": "title",
                    "captured_value": "Captured",
                    "captured_origin": "confirmed",
                    "user_value": None,
                    "user_override_set": False,
                }
            ],
            "latest_capture": {"adapter_version": "printables-v1", "snapshot": {}},
            "artifact_links": [cls._link()],
            **overrides,
        }

    @classmethod
    def _sidecar(cls, **source_overrides) -> dict:
        return {
            "models": [
                {"model_source_id": 7, "sources": [cls._source(**source_overrides)]}
            ]
        }

    def _contexts(self, sidecar: dict):
        return library_transfer._portable_provenance_contexts(sidecar, self._manifest())

    def test_returns_nothing_for_an_archive_with_no_sidecar(self) -> None:
        assert library_transfer._portable_provenance_contexts(None, {}) == {}

    def test_builds_a_context_for_each_artifact_link(self) -> None:
        contexts = self._contexts(self._sidecar())

        assert list(contexts) == [(7, 11)]

    def test_carries_the_captured_source_across(self) -> None:
        (context, _overrides) = self._contexts(self._sidecar())[(7, 11)][0]

        assert context.manifest.source.provider == "printables"
        assert context.source_filename == "cube.stl"

    def test_turns_a_marked_field_into_a_user_override(self) -> None:
        source = self._source(
            fields=[
                {
                    "field_name": "title",
                    "captured_value": "Captured",
                    "captured_origin": "confirmed",
                    "user_value": "Local",
                    "user_override_set": True,
                }
            ]
        )
        sidecar = {"models": [{"model_source_id": 7, "sources": [source]}]}

        (_context, overrides) = self._contexts(sidecar)[(7, 11)][0]

        assert overrides == {"title": "Local"}

    def test_reads_a_legacy_separate_overrides_list(self) -> None:
        source = self._source(
            overrides=[{"field_name": "description", "user_value": "Mine"}]
        )
        sidecar = {"models": [{"model_source_id": 7, "sources": [source]}]}

        (_context, overrides) = self._contexts(sidecar)[(7, 11)][0]

        assert overrides["description"] == "Mine"

    def test_invents_an_id_for_a_link_that_has_none(self) -> None:
        source = self._source(artifact_links=[self._link(source_file_id=None)])
        sidecar = {"models": [{"model_source_id": 7, "sources": [source]}]}

        (context, _overrides) = self._contexts(sidecar)[(7, 11)][0]

        # An older export may not have carried one; a stable synthetic id keeps
        # the link addressable rather than dropping it.
        assert context.source_selection_id == "portable-artifact-11"

    def test_delegates_a_v2_sidecar_to_the_v2_reader(self) -> None:
        sidecar = {"format": library_transfer.PROVENANCE_FORMAT, "models": []}

        # One entry point, two formats: the caller never chooses a reader.
        assert (
            library_transfer._portable_provenance_contexts(sidecar, self._manifest())
            == {}
        )

    @pytest.mark.parametrize(
        "model_row",
        [
            pytest.param({"sources": []}, id="missing-model-source-id"),
            pytest.param(
                {"model_source_id": 7, "sources": [], "extra": 1}, id="unknown-key"
            ),
            pytest.param(
                {"model_source_id": 7, "sources": "not-a-list"}, id="sources-not-a-list"
            ),
            pytest.param(
                {"model_source_id": "7", "sources": []}, id="model-source-id-not-an-int"
            ),
            pytest.param(
                {"model_source_id": True, "sources": []}, id="model-source-id-a-bool"
            ),
        ],
    )
    def test_refuses_a_model_row_it_does_not_recognise(self, model_row: dict) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts({"models": [model_row]})

    @pytest.mark.parametrize(
        "mutation",
        [
            pytest.param({"latest_capture": "not-a-dict"}, id="capture-not-a-dict"),
            pytest.param(
                {"latest_capture": {"adapter_version": "v1"}}, id="capture-missing-key"
            ),
            pytest.param(
                {"latest_capture": {"adapter_version": "v1", "snapshot": "no"}},
                id="snapshot-not-a-dict",
            ),
            pytest.param({"fields": "not-a-list"}, id="fields-not-a-list"),
            pytest.param({"artifact_links": "not-a-list"}, id="links-not-a-list"),
            pytest.param({"overrides": "not-a-list"}, id="overrides-not-a-list"),
        ],
    )
    def test_refuses_a_source_it_does_not_recognise(self, mutation: dict) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(**mutation))

    def test_refuses_a_source_that_is_not_an_object(self) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(
                {"models": [{"model_source_id": 7, "sources": ["not-a-dict"]}]}
            )

    def test_refuses_a_source_carrying_a_key_it_does_not_know(self) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(surprise=1))

    @pytest.mark.parametrize(
        "field",
        [
            pytest.param({"field_name": "title"}, id="missing-keys"),
            pytest.param(
                {
                    "field_name": 1,
                    "captured_value": None,
                    "captured_origin": None,
                    "user_value": None,
                    "user_override_set": False,
                },
                id="name-not-a-string",
            ),
            pytest.param(
                {
                    "field_name": "title",
                    "captured_value": None,
                    "captured_origin": None,
                    "user_value": None,
                    "user_override_set": "yes",
                },
                id="override-flag-not-a-bool",
            ),
        ],
    )
    def test_refuses_a_field_it_does_not_recognise(self, field: dict) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(fields=[field]))

    def test_refuses_the_same_field_declared_twice(self) -> None:
        field = {
            "field_name": "title",
            "captured_value": "One",
            "captured_origin": "confirmed",
            "user_value": None,
            "user_override_set": False,
        }

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(fields=[field, dict(field)]))

    @pytest.mark.parametrize(
        "override",
        [
            pytest.param({"field_name": "title"}, id="missing-value"),
            pytest.param(
                {"field_name": "not_a_real_field", "user_value": "x"},
                id="unknown-field",
            ),
            pytest.param({"field_name": 1, "user_value": "x"}, id="name-not-a-string"),
        ],
    )
    def test_refuses_a_legacy_override_it_does_not_recognise(
        self, override: dict
    ) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(overrides=[override]))

    def test_refuses_a_legacy_override_that_repeats_a_marked_field(self) -> None:
        source = self._source(
            fields=[
                {
                    "field_name": "title",
                    "captured_value": "Captured",
                    "captured_origin": "confirmed",
                    "user_value": "Local",
                    "user_override_set": True,
                }
            ],
            overrides=[{"field_name": "title", "user_value": "Other"}],
        )

        # Two answers for one field, and no rule about which wins.
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts({"models": [{"model_source_id": 7, "sources": [source]}]})

    @pytest.mark.parametrize(
        "mutation",
        [
            pytest.param({"artifact_source_id": 99}, id="unknown-artifact"),
            pytest.param({"artifact_source_id": True}, id="artifact-id-a-bool"),
            pytest.param({"source_filename": 1}, id="filename-not-a-string"),
            pytest.param({"source_file_id": 1}, id="file-id-not-a-string"),
            pytest.param({"container_entry_path": 1}, id="entry-path-not-a-string"),
            pytest.param({"container_entry_path": "../escape"}, id="entry-path-escape"),
            pytest.param({"source_revision": 1}, id="revision-not-a-string"),
            pytest.param({"blob_sha256": 1}, id="hash-not-a-string"),
            pytest.param({"blob_sha256": "b" * 64}, id="hash-of-another-artifact"),
            pytest.param({"source_filename": "../escape.stl"}, id="filename-escape"),
        ],
    )
    def test_refuses_an_artifact_link_it_does_not_trust(self, mutation: dict) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(artifact_links=[self._link(**mutation)]))

    def test_refuses_a_link_that_is_not_an_object(self) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(artifact_links=["not-a-dict"]))

    def test_refuses_a_link_carrying_a_key_it_does_not_know(self) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(artifact_links=[self._link(surprise=1)]))

    def test_refuses_the_same_link_declared_twice(self) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(artifact_links=[self._link(), self._link()]))


class TestPortableProvenanceContextsV2:
    """Reading the current provenance sidecar out of a portable archive.

    A v2 sidecar carries the **full capture snapshot** and a hash of it, and every
    relationship in it is checked against the library manifest before anything is
    written. That cross-check is the point: the sidecar and the manifest come from the
    same archive but are separate documents, so a forged or corrupted sidecar could
    otherwise attach one model's provenance to another model's bytes.

    The snapshot hash is checked against a canonical re-encoding rather than trusted,
    and the snapshot's own header must agree with the source row that carries it — a
    snapshot claiming a different provider than the source it sits under is the exact
    shape of that attack.

    Every artifact in a source's snapshot must match exactly one link and one manifest
    artifact, in both directions. Importing one member of a multi-file capture must not
    silently collapse the history to a one-file source.
    """

    ARTIFACT = {
        "source_id": 11,
        "sha256": "a" * 64,
        "file_type": "stl",
        "size_bytes": 1,
    }
    SNAPSHOT_FIELDS = {"title": {"origin": "confirmed", "value": "Captured"}}

    @classmethod
    def _manifest(cls) -> dict:
        return {"models": [{"source_id": 7, "artifacts": [dict(cls.ARTIFACT)]}]}

    @staticmethod
    def _hash(snapshot: dict) -> str:
        import hashlib as _hashlib

        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return _hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _snapshot(cls, **overrides) -> dict:
        return {
            "provider": "printables",
            "canonical_url": "https://www.printables.com/model/42",
            "source_item_id": "42",
            "source_revision": None,
            "tags": [],
            "fields": dict(cls.SNAPSHOT_FIELDS),
            "files": [
                {
                    "source_selection_id": "42:cube",
                    "source_file_id": "42:cube",
                    "source_filename": "cube.stl",
                }
            ],
            **overrides,
        }

    @classmethod
    def _link(cls, **overrides) -> dict:
        return {
            "artifact_source_id": 11,
            "source_file_id": "42:cube",
            "source_filename": "cube.stl",
            "container_entry_path": None,
            "source_revision": None,
            "blob_sha256": cls.ARTIFACT["sha256"],
            **overrides,
        }

    @classmethod
    def _source(cls, *, snapshot: dict | None = None, **overrides) -> dict:
        snapshot = snapshot if snapshot is not None else cls._snapshot()
        return {
            "source_id": 3,
            "provider": snapshot["provider"],
            "canonical_url": snapshot["canonical_url"],
            "source_item_id": snapshot["source_item_id"],
            "source_revision": snapshot["source_revision"],
            "fields": [
                {
                    "field_name": "title",
                    "captured_value": "Captured",
                    "captured_origin": "confirmed",
                    "user_value": None,
                    "user_override_set": False,
                }
            ],
            "latest_capture": {
                "adapter_version": "printables-v1",
                "snapshot": snapshot,
                "snapshot_sha256": cls._hash(snapshot),
            },
            "artifact_links": [cls._link()],
            **overrides,
        }

    @classmethod
    def _sidecar(cls, **source_overrides) -> dict:
        return {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [
                {"model_source_id": 7, "sources": [cls._source(**source_overrides)]}
            ],
        }

    def _contexts(self, sidecar: dict):
        return library_transfer._portable_v2_provenance_contexts(
            sidecar, self._manifest()
        )

    def test_builds_a_context_for_each_artifact_link(self) -> None:
        assert list(self._contexts(self._sidecar())) == [(7, 11)]

    def test_carries_the_whole_capture_snapshot(self) -> None:
        (context, _overrides) = self._contexts(self._sidecar())[(7, 11)][0]

        assert context.manifest.source.provider == "printables"
        assert context.source_selection_id == "42:cube"

    def test_turns_a_marked_field_into_a_user_override(self) -> None:
        source = self._source(
            fields=[
                {
                    "field_name": "title",
                    "captured_value": "Captured",
                    "captured_origin": "confirmed",
                    "user_value": "Local",
                    "user_override_set": True,
                }
            ]
        )
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        (_context, overrides) = self._contexts(sidecar)[(7, 11)][0]

        assert overrides == {"title": "Local"}

    def test_accepts_a_source_that_also_carries_a_cover(self) -> None:
        sidecar = self._sidecar(
            cover={
                "entry": "covers/1.webp",
                "content_type": "image/webp",
                "size_bytes": 1,
                "sha256": "0" * 64,
            }
        )

        assert list(self._contexts(sidecar)) == [(7, 11)]

    def test_refuses_a_source_carrying_a_key_it_does_not_know(self) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(surprise=1))

    @pytest.mark.parametrize(
        "source_id", [pytest.param("3", id="string"), pytest.param(True, id="bool")]
    )
    def test_refuses_a_source_id_that_is_not_an_integer(
        self, source_id: object
    ) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(source_id=source_id))

    def test_refuses_the_same_source_declared_twice(self) -> None:
        source = self._source()
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [
                {
                    "model_source_id": 7,
                    "sources": [source, json.loads(json.dumps(source))],
                }
            ],
        }

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    def test_refuses_a_snapshot_hash_that_does_not_match_the_snapshot(self) -> None:
        source = self._source()
        source["latest_capture"]["snapshot_sha256"] = "b" * 64
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        # The hash is re-computed from a canonical encoding, never trusted.
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    @pytest.mark.parametrize(
        "mutation",
        [
            pytest.param({"provider": "thingiverse"}, id="provider"),
            pytest.param({"canonical_url": "https://elsewhere.test/1"}, id="url"),
            pytest.param({"source_item_id": "99"}, id="item-id"),
            pytest.param({"source_revision": "r9"}, id="revision"),
        ],
    )
    def test_refuses_a_snapshot_that_disagrees_with_its_source_row(
        self, mutation: dict
    ) -> None:
        snapshot = self._snapshot(**mutation)
        source = self._source()
        source["latest_capture"]["snapshot"] = snapshot
        source["latest_capture"]["snapshot_sha256"] = self._hash(snapshot)
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        # A snapshot claiming a different origin than the row it sits under is
        # exactly how one model's provenance would be attached to another's bytes.
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    @pytest.mark.parametrize(
        "mutation",
        [
            pytest.param({"tags": "not-a-list"}, id="tags-not-a-list"),
            pytest.param({"fields": "not-a-dict"}, id="fields-not-a-dict"),
            pytest.param({"files": "not-a-list"}, id="files-not-a-list"),
            pytest.param({"files": []}, id="no-files"),
        ],
    )
    def test_refuses_a_snapshot_it_does_not_recognise(self, mutation: dict) -> None:
        snapshot = self._snapshot(**mutation)
        source = self._source()
        source["latest_capture"]["snapshot"] = snapshot
        source["latest_capture"]["snapshot_sha256"] = self._hash(snapshot)
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    def test_refuses_a_snapshot_carrying_a_key_it_does_not_know(self) -> None:
        snapshot = self._snapshot(surprise=1)
        source = self._source()
        source["latest_capture"]["snapshot"] = snapshot
        source["latest_capture"]["snapshot_sha256"] = self._hash(snapshot)
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    def test_refuses_a_capture_missing_its_snapshot_hash(self) -> None:
        source = self._source()
        del source["latest_capture"]["snapshot_sha256"]
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    def test_refuses_a_field_set_that_disagrees_with_the_snapshot(self) -> None:
        source = self._source(
            fields=[
                {
                    "field_name": "description",
                    "captured_value": "Elsewhere",
                    "captured_origin": "confirmed",
                    "user_value": None,
                    "user_override_set": False,
                }
            ]
        )
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        # The rows and the snapshot are two copies of one truth; they must agree.
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    def test_refuses_a_field_whose_value_disagrees_with_the_snapshot(self) -> None:
        source = self._source(
            fields=[
                {
                    "field_name": "title",
                    "captured_value": "Different",
                    "captured_origin": "confirmed",
                    "user_value": None,
                    "user_override_set": False,
                }
            ]
        )
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    def test_refuses_a_source_with_no_artifact_links(self) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(artifact_links=[]))

    def test_refuses_a_link_carrying_a_key_it_does_not_know(self) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(artifact_links=[self._link(surprise=1)]))

    @pytest.mark.parametrize(
        "snapshot_file",
        [
            pytest.param({"source_file_id": "42:cube"}, id="missing-keys"),
            pytest.param(
                {
                    "source_selection_id": "42:cube",
                    "source_file_id": 1,
                    "source_filename": "cube.stl",
                },
                id="file-id-not-a-string",
            ),
            pytest.param(
                {
                    "source_selection_id": "42:cube",
                    "source_file_id": "42:cube",
                    "source_filename": "../escape.stl",
                },
                id="filename-escape",
            ),
        ],
    )
    def test_refuses_a_snapshot_file_it_does_not_trust(
        self, snapshot_file: dict
    ) -> None:
        snapshot = self._snapshot(files=[snapshot_file])
        source = self._source()
        source["latest_capture"]["snapshot"] = snapshot
        source["latest_capture"]["snapshot_sha256"] = self._hash(snapshot)
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    def test_refuses_a_snapshot_file_that_no_link_names(self) -> None:
        snapshot = self._snapshot(
            files=[
                {
                    "source_selection_id": "42:other",
                    "source_file_id": "42:other",
                    "source_filename": "other.stl",
                }
            ]
        )
        source = self._source()
        source["latest_capture"]["snapshot"] = snapshot
        source["latest_capture"]["snapshot_sha256"] = self._hash(snapshot)
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)

    @pytest.mark.parametrize(
        "mutation",
        [
            pytest.param({"artifact_source_id": 99}, id="unknown-artifact"),
            pytest.param({"artifact_source_id": True}, id="artifact-id-a-bool"),
            pytest.param({"blob_sha256": "b" * 64}, id="hash-of-another-artifact"),
            pytest.param({"container_entry_path": "../escape"}, id="entry-path-escape"),
            pytest.param({"source_revision": "r9"}, id="revision-disagrees"),
        ],
    )
    def test_refuses_an_artifact_link_it_does_not_trust(self, mutation: dict) -> None:
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(self._sidecar(artifact_links=[self._link(**mutation)]))

    def test_refuses_two_links_pointing_at_the_same_artifact(self) -> None:
        snapshot = self._snapshot(
            files=[
                {
                    "source_selection_id": "42:cube",
                    "source_file_id": "42:cube",
                    "source_filename": "cube.stl",
                },
                {
                    "source_selection_id": "42:cube2",
                    "source_file_id": "42:cube2",
                    "source_filename": "cube2.stl",
                },
            ]
        )
        source = self._source()
        source["latest_capture"]["snapshot"] = snapshot
        source["latest_capture"]["snapshot_sha256"] = self._hash(snapshot)
        source["artifact_links"] = [
            self._link(),
            self._link(source_file_id="42:cube2", source_filename="cube2.stl"),
        ]
        sidecar = {
            "format": library_transfer.PROVENANCE_FORMAT,
            "models": [{"model_source_id": 7, "sources": [source]}],
        }

        # Two snapshot files claiming one artifact would give that artifact two
        # different provenance histories.
        with pytest.raises(ValueError, match="portable_provenance_invalid"):
            self._contexts(sidecar)


class TestCreateArchive:
    def test_library_archive_round_trips_override_for_absent_captured_field(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
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
            imported = db_session.exec(
                select(Model).where(Model.hash == "e" * 64)
            ).one()
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

    def test_library_archive_export_preflights_import_entry_limit(
        self,
        db_session: Session,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user, _, _ = _seed(db_session, tmp_path)
        monkeypatch.setattr(library_transfer, "MAX_ENTRIES", 1)

        with pytest.raises(ValueError, match="archive_too_large"):
            library_transfer.create_archive(db_session, user)

    def test_library_archive_limit_covers_large_library_reference(self) -> None:
        assert library_transfer.MAX_ENTRIES >= 25_001

    def test_library_archive_export_rejects_changed_source_blob(
        self,
        db_session: Session,
        auth_headers: dict[str, str],
        tmp_path: Path,
    ) -> None:
        user, _, file_row = _seed(db_session, tmp_path)
        source = Path(file_row.path)
        source.write_bytes(b"x" * file_row.size_bytes)

        with pytest.raises(ValueError, match="archive_blob_hash_mismatch"):
            library_transfer.create_archive(db_session, user)

    def test_library_archive_api_downloads_zip(
        self,
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
        self,
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

    def test_library_archive_export_does_not_build_metadata_export_first(
        self,
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

    def test_library_archive_emits_optional_prevalidated_provenance_sidecar(
        self, db_session: Session, auth_headers: dict[str, str], tmp_path: Path
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
                sidecar["models"][0]["sources"][0]["artifact_links"][0][
                    "source_file_id"
                ]
                == "42:cube"
            )
            assert "actor" not in json.dumps(sidecar).lower()
        finally:
            archive_path.unlink(missing_ok=True)
