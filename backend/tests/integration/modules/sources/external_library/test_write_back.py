"""A web upload routed into an external library instead of the vault.

Write-back is the one path in this feature that *writes* to somebody else's
folder, so it is the one with the most to lose. Three properties make it safe
enough to ship, and each is a test here.

It must not clobber. A user's hand-placed `part.gcode` is not a file PrintStash
may overwrite because an upload happens to share its name, so a collision lands
under a new name and the original bytes stay byte-identical.

It must not escape. In MIRROR mode the collection name becomes a directory path
under the root, and a symlink already sitting in the share can point that path
anywhere on the host — so the resolved destination is checked against the root and
a traversal fails the job instead of writing outside it.

And it must not happen at all while the feature is off. `target_library_id` is
set by the client; honouring it without checking the flag would let a request
write to a NAS on an installation whose operator never enabled the feature.

Revisions follow their model: a new revision of a model that lives in a library
is written back beside it, not stranded in the vault."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import (
    ExternalLibrary,
    ExternalLibraryCollectionMode,
    File,
    FileType,
    JobKind,
    Model,
    OwnedStorageObject,
    StorageDeleteIntent,
)
from app.modules.ingestion.ingestion import (
    StagedArtifact,
    add_gcode_revision_to_model,
    ingest_staged_file,
)
from app.modules.sources import external_library
from app.modules.work.jobs import jobs
from tests._env import use_local_storage
from tests.factories import build_external_library, build_job
from tests.factories.ops import build_job_context
from tests.integration.modules.sources.external_library._helpers import (
    FIXTURE_GCODE,
    drop_gcode,
    enable_feature,
    external_files,
    gcode_bytes,
    stage,
)


def _upload(
    session: Session,
    staged: Path,
    *,
    filename: str,
    name: str,
    collection: str | None = None,
    library: ExternalLibrary | None = None,
) -> str:
    """Commit one staged G-code upload the way its ``ingestion.upload`` Job does."""
    job = build_job(session, kind=JobKind.INGESTION_UPLOAD)
    ingest_staged_file(
        job_context=build_job_context(job.id),
        artifact=StagedArtifact(
            staged_path=staged,
            original_filename=filename,
            model_name=name,
            file_type=FileType.GCODE,
            collection=collection,
            target_library_id=library.id if library is not None else None,
        ),
        actor_user_id=None,
    )
    return job.id


class TestIngestIntoExternalLibrary:
    def test_write_back_uses_existing_folder_spelling(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(nas / "Testing" / "My Parts", "existing.gcode", marker="existing")
        lib = build_external_library(
            db_session,
            nas,
            name="nas",
            collection_mode=ExternalLibraryCollectionMode.MIRROR,
        )
        external_library.scan_library(lib.id)
        existing = db_session.exec(
            select(File).where(File.original_filename == "existing.gcode")
        ).one()
        model = db_session.get(Model, existing.model_id)
        assert model.collection_rel is not None

        _upload(
            db_session,
            stage("new.gcode", gcode_bytes("new")),
            filename="new.gcode",
            name="New Model",
            collection=model.collection_rel.path,
            library=lib,
        )

        written = db_session.exec(
            select(File).where(File.original_filename == "new.gcode")
        ).one()
        assert written.path == str(nas / "Testing" / "My Parts" / "new.gcode")
        assert not (nas / "testing").exists()

    def test_write_back_lands_in_nas_folder(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        nas.mkdir(parents=True)
        lib = build_external_library(
            db_session,
            nas,
            name="nas",
            collection_mode=ExternalLibraryCollectionMode.MIRROR,
        )

        # Stage an upload and route it into the library (write-back).
        staged = (
            Path(_overlay["staging_dir"]) / "_incoming" / f"{uuid.uuid4().hex}.gcode"
        )
        shutil.copy(FIXTURE_GCODE, staged)
        _upload(
            db_session,
            staged,
            filename="written.gcode",
            name="Written Model",
            collection="cool/widgets",
            library=lib,
        )

        f = db_session.exec(select(File).where(File.is_external == True)).first()  # noqa: E712
        assert f is not None
        assert f.external_library_id == lib.id
        # Physically written under the library root, mirrored into the collection path.
        assert f.path.startswith(str(nas))
        assert Path(f.path).exists()
        assert "cool/widgets" in f.path.replace("\\", "/")
        assert not staged.exists()  # staged upload was moved, not left behind

        # A subsequent scan recognises the written file as unchanged.
        summary = external_library.scan_library(lib.id)
        assert summary["added"] == 0
        assert summary["skipped"] == 1

    def test_revision_is_written_back_into_library(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(nas, "bracket.gcode", marker="v1")
        lib = build_external_library(
            db_session,
            nas,
            name="nas",
            collection_mode=ExternalLibraryCollectionMode.MIRROR,
        )
        external_library.scan_library(lib.id)
        model = db_session.get(Model, external_files(db_session)[0].model_id)

        staged = stage("bracket-v2.gcode", gcode_bytes("v2"))
        rev = add_gcode_revision_to_model(
            session=db_session,
            model=model,
            staged_path=staged,
            original_filename="bracket-v2.gcode",
            revision_label="v2",
            revision_status=None,
            revision_notes=None,
            is_recommended=False,
        )

        assert rev.is_external is True
        assert rev.external_library_id == lib.id
        assert rev.path.startswith(str(nas))
        assert Path(rev.path).exists()
        assert not staged.exists()  # staged upload moved onto the NAS, not copied

        # The next scan recognises the written-back revision as already-indexed.
        summary = external_library.scan_library(lib.id)
        assert summary["added"] == 0

    def test_revision_keeps_nas_bytes_outside_vault_lifecycle_ledgers(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(nas, "bracket.gcode", marker="ledger-v1")
        lib = build_external_library(
            db_session,
            nas,
            name="nas-ledger",
            collection_mode=ExternalLibraryCollectionMode.MIRROR,
        )
        external_library.scan_library(lib.id)
        model = db_session.get(Model, external_files(db_session)[0].model_id)
        staged = stage("bracket-v2.gcode", gcode_bytes("ledger-v2"))

        revision = add_gcode_revision_to_model(
            session=db_session,
            model=model,
            staged_path=staged,
            original_filename="bracket-v2.gcode",
            revision_label="v2",
            revision_status=None,
            revision_notes=None,
            is_recommended=False,
        )

        owned = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == revision.path)
        ).all()
        delete_intents = db_session.exec(
            select(StorageDeleteIntent).where(StorageDeleteIntent.key == revision.path)
        ).all()
        assert Path(revision.path).read_bytes() == gcode_bytes("ledger-v2")
        assert owned == []
        assert delete_intents == []

    def test_write_back_never_overwrites_existing_nas_file(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        """A web upload routed into the NAS must not clobber a same-named file the
        user already has there — it lands under a collision-safe name instead."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        nas.mkdir(parents=True)
        precious = nas / "part.gcode"
        precious.write_bytes(b"; HAND-PLACED USER FILE - do not touch\n")
        lib = build_external_library(
            db_session,
            nas,
            name="nas",
            collection_mode=ExternalLibraryCollectionMode.MIRROR,
        )

        staged = stage("part.gcode", gcode_bytes("upload"))
        _upload(db_session, staged, filename="part.gcode", name="Part", library=lib)

        # Original bytes untouched.
        assert precious.read_bytes() == b"; HAND-PLACED USER FILE - do not touch\n"
        # New upload written beside it under a non-clobbering name.
        f = external_files(db_session, live_only=False)[0]
        assert Path(f.path).name == "part-2.gcode"
        assert Path(f.path).exists()

    def test_feature_disabled_keeps_uploads_in_vault(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        # Feature OFF (default). Even with a target_library_id the blob stays in vault.
        nas = tmp_path / "nas"
        nas.mkdir(parents=True)
        lib = build_external_library(db_session, nas, name="nas")

        staged = (
            Path(_overlay["staging_dir"]) / "_incoming" / f"{uuid.uuid4().hex}.gcode"
        )
        shutil.copy(FIXTURE_GCODE, staged)
        _upload(
            db_session, staged, filename="vaulted.gcode", name="Vaulted", library=lib
        )

        f = db_session.exec(
            select(File).where(File.original_filename == "vaulted.gcode")
        ).first()
        assert f is not None
        assert f.is_external is False
        assert f.path.startswith(str(_overlay["data_dir"]))

    def test_write_back_rejects_collection_symlink_escape(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        """A mirrored collection symlink cannot redirect a write outside the NAS."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        outside = tmp_path / "outside"
        nas.mkdir(parents=True)
        outside.mkdir()
        (nas / "escaped").symlink_to(outside, target_is_directory=True)
        lib = build_external_library(
            db_session,
            nas,
            name="nas",
            collection_mode=ExternalLibraryCollectionMode.MIRROR,
        )

        staged = stage("part.gcode", gcode_bytes("escape"))
        job_id = _upload(
            db_session,
            staged,
            filename="part.gcode",
            name="Part",
            collection="escaped",
            library=lib,
        )

        job = jobs.get(job_id)
        assert job is not None
        assert job.state == "failed"
        assert job.error == "external_library_symlink_escape"
        assert not (outside / "part.gcode").exists()
