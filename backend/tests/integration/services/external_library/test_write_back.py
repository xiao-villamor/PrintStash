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
    ExternalLibraryCollectionMode,
    File,
    Model,
)
from app.services import external_library
from app.services.ingestion import add_gcode_revision_to_model, ingest_orca_gcode
from app.services.jobs import registry
from tests._env import use_local_storage
from tests.factories import build_external_library
from tests.integration.services.external_library._helpers import (
    FIXTURE_GCODE,
    drop_gcode,
    enable_feature,
    external_files,
    gcode_bytes,
    stage,
)


class TestIngestIntoExternalLibrary:
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
        ingest_orca_gcode(
            job_id="job-wb",
            staged_path=staged,
            original_filename="written.gcode",
            model_name="Written Model",
            collection="cool/widgets",
            tags=None,
            source_hash=None,
            target_library_id=lib.id,
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
        ingest_orca_gcode(
            job_id="job-collision",
            staged_path=staged,
            original_filename="part.gcode",
            model_name="Part",
            collection=None,
            tags=None,
            source_hash=None,
            target_library_id=lib.id,
        )

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
        ingest_orca_gcode(
            job_id="job-vault",
            staged_path=staged,
            original_filename="vaulted.gcode",
            model_name="Vaulted",
            collection=None,
            tags=None,
            source_hash=None,
            target_library_id=lib.id,
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
        job_id = registry.create()
        ingest_orca_gcode(
            job_id=job_id,
            staged_path=staged,
            original_filename="part.gcode",
            model_name="Part",
            collection="escaped",
            tags=None,
            source_hash=None,
            target_library_id=lib.id,
        )

        job = registry.get(job_id)
        assert job is not None
        assert job.state == "failed"
        assert job.error == "external_library_symlink_escape"
        assert not (outside / "part.gcode").exists()
