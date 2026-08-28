"""Deletion inside PrintStash never reaching the bytes on the NAS.

An external file is a row that describes somebody else's file. Everything that
deletes in PrintStash — trashing a model, the retention purge that hard-deletes
it, the scheduled GC that reclaims storage — was written for vault files, where
removing the row and removing the blob are the same operation. Applied to an
external row, that is data loss on a filesystem PrintStash does not own, and it
is unrecoverable: there is no trash on the user's NAS.

The two tests here are the reason the feature is safe to enable. GC at zero
retention leaves external blobs alone, and a full hard delete removes the File
and Model rows while the original file and its exact bytes survive. Both assert
on the bytes, not just on the file's existence, because a truncated file exists."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from app.db.models import (
    File,
    Model,
)
from app.services import external_library, trash
from tests._env import use_local_storage
from tests.factories import build_external_library
from tests.integration.services.external_library._helpers import (
    drop_gcode,
    enable_feature,
    external_files,
    gcode_bytes,
)


class TestGcSoftDeleted:
    def test_gc_never_deletes_external_blobs(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        """External files are user-owned and survive GC, even at zero retention."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        path = drop_gcode(nas, "onnas.gcode", marker="gc")
        lib = build_external_library(db_session, nas, name="nas")
        external_library.scan_library(lib.id)

        trash.gc_soft_deleted(retention_days=0)

        assert path.exists()
        assert path.read_bytes() == gcode_bytes("gc")
        # The index row is still live (the file was never trashed).
        assert len(external_files(db_session)) == 1


class TestHardDeleteModel:
    def test_hard_delete_model_never_destroys_nas_bytes(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        """Trash retention purge (hard delete) removes DB rows + vault thumbnails but
        must leave the original file on the NAS completely untouched."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        path = drop_gcode(nas, "precious.gcode", marker="keep-me")
        original_bytes = path.read_bytes()
        lib = build_external_library(db_session, nas, name="nas")
        external_library.scan_library(lib.id)

        f = external_files(db_session)[0]
        model = db_session.get(Model, f.model_id)

        trash.soft_delete_model(db_session, model)
        trash.hard_delete_model(db_session, model)
        db_session.commit()

        # DB rows are gone...
        assert db_session.get(File, f.id) is None
        assert db_session.get(Model, model.id) is None
        # ...but the NAS file and its exact bytes survive.
        assert path.exists()
        assert path.read_bytes() == original_bytes
