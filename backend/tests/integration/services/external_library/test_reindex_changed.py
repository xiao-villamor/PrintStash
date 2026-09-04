"""`_reindex_changed` refusing to record a signature it did not finish writing.

The scan skips unchanged files by comparing the stored size and mtime, so the
stored signature is a *claim* that the row's hash and metadata match what is on
disk. Confirming the new signature before the metadata write succeeds inverts
that: the file's content is now permanently misdescribed, and every future scan
skips it because the signature says it is up to date. Stale metadata that no
amount of rescanning can fix is worse than a failed scan.

So the write order is the behaviour under test — the signature lands last, and a
metadata failure leaves the old signature intact so the next scan tries again."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import (
    File,
    Metadata,
)
from app.services import external_library
from tests._env import use_local_storage
from tests.factories import build_external_library
from tests.integration.services.external_library._helpers import (
    drop_gcode,
)


class TestReindexChanged:
    def test_reindex_metadata_failure_does_not_confirm_new_signature(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch,
    ) -> None:
        use_local_storage(tmp_path)
        nas = tmp_path / "nas"
        path = drop_gcode(nas, "atomic.gcode")
        lib = build_external_library(db_session, nas, name="nas")
        external_library.scan_library(lib.id)
        file_row = db_session.exec(
            select(File).where(File.original_filename == "atomic.gcode")
        ).one()
        old_hash = file_row.sha256
        old_size = file_row.size_bytes
        with path.open("ab") as handle:
            handle.write(b"\n; changed for atomicity test\n")
        stat = path.stat()

        real_add = db_session.add

        def fail_metadata_add(instance, *args, **kwargs):
            if isinstance(instance, Metadata):
                raise RuntimeError("metadata_write_failed")
            return real_add(instance, *args, **kwargs)

        monkeypatch.setattr(db_session, "add", fail_metadata_add)
        with pytest.raises(RuntimeError, match="metadata_write_failed"):
            external_library._reindex_changed(
                db_session, file_row, path, stat.st_size, stat.st_mtime
            )
        db_session.rollback()
        db_session.refresh(file_row)

        assert file_row.sha256 == old_hash
        assert file_row.size_bytes == old_size
