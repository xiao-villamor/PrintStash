"""`_reindex_changed` refusing to record a signature it did not finish writing.

The scan skips unchanged files by comparing the stored size and mtime, so the
stored signature is a *claim* that what was derived from the row matches what is
on disk. Confirming the new signature without invalidating the derivatives of
the old bytes inverts that: the file's metadata and thumbnail describe content
that is gone, and every future scan skips it because the signature says it is up
to date. Stale derivatives that no amount of rescanning can fix are worse than a
failed scan.

So the new hash and the invalidation land in one commit, and a failure in
between leaves the old signature intact so the next scan tries again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import DerivativeKind, DerivativeState, File
from app.modules.derivatives import records
from app.modules.sources import external_library
from app.runtime.engine.inline import InlineJobEngine
from tests._env import use_local_storage
from tests.factories import build_external_library
from tests.integration.modules.sources.external_library._helpers import (
    drop_gcode,
)


def _indexed(tmp_path: Path, session: Session) -> tuple[Path, File]:
    use_local_storage(tmp_path)
    nas = tmp_path / "nas"
    path = drop_gcode(nas, "atomic.gcode")
    lib = build_external_library(session, nas, name="nas")
    external_library.scan_library(lib.id)
    file_row = session.exec(
        select(File).where(File.original_filename == "atomic.gcode")
    ).one()
    return path, file_row


def _edit(path: Path) -> None:
    with path.open("ab") as handle:
        handle.write(b"\n; changed for atomicity test\n")


class TestReindexChanged:
    def test_forgets_the_derivatives_of_the_old_bytes(
        self,
        tmp_path: Path,
        db_session: Session,
        work_engine: InlineJobEngine,
        make_derivative,
    ) -> None:
        path, file_row = _indexed(tmp_path, db_session)
        make_derivative(file_row, DerivativeKind.METADATA, state=DerivativeState.READY)
        _edit(path)
        stat = path.stat()

        external_library._reindex_changed(
            db_session, file_row, path, stat.st_size, stat.st_mtime
        )

        assert DerivativeKind.METADATA not in records.rows_for(db_session, file_row)

    def test_rederives_the_changed_bytes(
        self, tmp_path: Path, db_session: Session, work_engine: InlineJobEngine
    ) -> None:
        path, file_row = _indexed(tmp_path, db_session)
        work_engine.drain()
        _edit(path)
        stat = path.stat()

        external_library._reindex_changed(
            db_session, file_row, path, stat.st_size, stat.st_mtime
        )
        work_engine.drain()

        db_session.expire_all()
        row = records.rows_for(db_session, db_session.get(File, file_row.id))[
            DerivativeKind.METADATA
        ]
        assert row.state == DerivativeState.READY

    def test_keeps_the_old_signature_when_invalidation_fails(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path, file_row = _indexed(tmp_path, db_session)
        old_hash = file_row.sha256
        old_size = file_row.size_bytes
        _edit(path)
        stat = path.stat()

        def fail_invalidate(*_args, **_kwargs) -> int:
            raise RuntimeError("invalidation_failed")

        monkeypatch.setattr(records, "invalidate", fail_invalidate)
        with pytest.raises(RuntimeError, match="invalidation_failed"):
            external_library._reindex_changed(
                db_session, file_row, path, stat.st_size, stat.st_mtime
            )
        db_session.rollback()
        db_session.refresh(file_row)

        assert (file_row.sha256, file_row.size_bytes) == (old_hash, old_size)

    def test_only_records_the_signature_when_just_the_mtime_moved(
        self, tmp_path: Path, db_session: Session, make_derivative
    ) -> None:
        path, file_row = _indexed(tmp_path, db_session)
        make_derivative(file_row, DerivativeKind.METADATA, state=DerivativeState.READY)
        stat = path.stat()

        changed = external_library._reindex_changed(
            db_session, file_row, path, stat.st_size, stat.st_mtime + 60
        )

        assert changed is False
        assert DerivativeKind.METADATA in records.rows_for(db_session, file_row)


class TestTerminalMeshFailure:
    @pytest.mark.parametrize("trigger", ["watcher", "periodic"])
    def test_unchanged_mesh_failure_survives_source_refresh(
        self, tmp_path, db_session, work_engine, trigger
    ):
        import os

        from app.modules.sources.library_watcher import LibraryWatcher

        use_local_storage(tmp_path)
        root = tmp_path / "mounted"
        root.mkdir()
        path = root / "broken.3mf"
        path.write_bytes(b"malformed mesh package")
        library = build_external_library(db_session, root, name="mounted")
        external_library.scan_library(library.id)
        work_engine.drain()
        file_row = db_session.exec(
            select(File).where(File.original_filename == "broken.3mf")
        ).one()
        before = records.rows_for(db_session, file_row)[DerivativeKind.METADATA]
        assert before.state == DerivativeState.FAILED
        attempts, updated = before.attempts, before.updated_at
        for _ in range(3):
            stat = path.stat()
            os.utime(path, (stat.st_atime, stat.st_mtime + 60))
            if trigger == "watcher":
                LibraryWatcher._request_scan(library.id)
            else:
                external_library.scan_library(library.id)
            work_engine.drain()
        db_session.expire_all()
        after = records.rows_for(db_session, db_session.get(File, file_row.id))[
            DerivativeKind.METADATA
        ]
        assert (after.attempts, after.updated_at) == (attempts, updated)

    def test_changed_mesh_bytes_become_eligible_again(
        self, tmp_path, db_session, work_engine
    ):
        from tests.factories.geometry import three_mf

        use_local_storage(tmp_path)
        root = tmp_path / "mounted"
        root.mkdir()
        path = root / "changed.3mf"
        path.write_bytes(b"malformed mesh package")
        library = build_external_library(db_session, root, name="mounted")
        external_library.scan_library(library.id)
        work_engine.drain()
        file_row = db_session.exec(
            select(File).where(File.original_filename == "changed.3mf")
        ).one()
        assert (
            records.rows_for(db_session, file_row)[DerivativeKind.METADATA].state
            == DerivativeState.FAILED
        )
        path.write_bytes(three_mf())
        external_library.scan_library(library.id)
        work_engine.drain()
        db_session.expire_all()
        assert (
            records.rows_for(db_session, db_session.get(File, file_row.id))[
                DerivativeKind.METADATA
            ].state
            == DerivativeState.READY
        )
