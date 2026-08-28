"""The scan refusing to conclude that an unreachable share was emptied.

This is the file that stands between a user and the worst thing this feature
could do. A scan decides what to remove by diffing the folder against the index,
so a share that failed to mount presents as "every file was deleted" — and the
naive reconcile would soft-delete the user's entire library on a network hiccup.
The guard is to abort instead: an unmounted root, or an empty root that used to
hold indexed files, is a failed scan rather than a successful deletion.

The rest is refusing to strand state. A scan takes a claim so two of them cannot
interleave over the same rows, which means an unexpected exception must still
release it — a library stuck in RUNNING never scans again, and nothing in the UI
explains why. And one bad file is not a bad share: a single unparseable or locked
file is recorded in `errors` and the scan finishes as PARTIAL, which is honest,
rather than green (a lie) or aborted (throwing away the rest of the folder)."""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    ExternalLibraryScanStatus,
    File,
)
from app.db.scopes import live
from app.services import external_library
from tests._env import use_local_storage
from tests.factories import build_external_library
from tests.integration.services.external_library._helpers import (
    drop_gcode,
    enable_feature,
    external_files,
)


class TestScanLibrary:
    def test_unmounted_root_aborts_without_deleting(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(nas, "keep.gcode")
        lib = build_external_library(db_session, nas, name="nas")
        external_library.scan_library(lib.id)

        # Simulate an unmount: the whole root disappears.
        shutil.rmtree(nas)

        summary = external_library.scan_library(lib.id)

        assert summary["aborted"] is True
        assert summary["removed"] == 0
        # Nothing was trashed.
        live_files = db_session.exec(
            select(File).where(File.is_external == True, live(File))  # noqa: E712
        ).all()
        assert len(live_files) == 1
        db_session.refresh(lib)
        assert lib.last_scan_status == ExternalLibraryScanStatus.ERROR

    def test_empty_root_with_indexed_files_aborts(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        path = drop_gcode(nas, "keep.gcode")
        lib = build_external_library(db_session, nas, name="nas")
        external_library.scan_library(lib.id)

        # Root still exists but is empty (e.g. NAS share mounted but unpopulated).
        path.unlink()

        summary = external_library.scan_library(lib.id)

        assert summary["aborted"] is True
        live_files = db_session.exec(
            select(File).where(File.is_external == True, live(File))  # noqa: E712
        ).all()
        assert len(live_files) == 1

    def test_scan_coalesces_while_another_claim_is_active(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        nas = tmp_path / "nas"
        drop_gcode(nas, "a.gcode")
        lib = build_external_library(db_session, nas, name="nas")
        lib.scan_claim_token = "active-claim"
        lib.scan_claim_expires_at = utcnow() + timedelta(minutes=5)
        lib.scan_job_id = "existing-job"
        db_session.add(lib)
        db_session.commit()

        result = external_library.scan_library(lib.id, job_id="duplicate-job")

        assert result == {"coalesced": True, "job_id": "existing-job"}
        assert external_files(db_session, live_only=False) == []

    def test_a_scan_continues_past_a_file_that_fails(
        self, tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One unparseable/locked file must not abort the whole NAS sync: it is
        recorded in ``errors`` while the rest of the folder still indexes."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(nas, "good-1.gcode", marker="g1")
        drop_gcode(nas, "bad.gcode", marker="g2")
        drop_gcode(nas, "good-2.gcode", marker="g3")
        lib = build_external_library(db_session, nas, name="nas")

        real_index = external_library._index_external_file

        def flaky(session, library, source_path, size, mtime):  # type: ignore[no-untyped-def]
            if source_path.name == "bad.gcode":
                raise RuntimeError("simulated parse failure")
            return real_index(session, library, source_path, size, mtime)

        monkeypatch.setattr(external_library, "_index_external_file", flaky)

        summary = external_library.scan_library(lib.id)

        assert summary["added"] == 2
        assert summary["aborted"] is False
        assert len(summary["errors"]) == 1
        assert "bad.gcode" in summary["errors"][0]
        db_session.refresh(lib)
        # A completed-with-failures scan is PARTIAL, not a misleading green OK.
        assert lib.last_scan_status == ExternalLibraryScanStatus.PARTIAL

    def test_unexpected_failure_never_strands_scan_running(
        self, tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exception outside the per-file boundary (e.g. a NAS mount dropping
        mid-walk) must land the library in a terminal ERROR state, not leave it
        stranded RUNNING where the scheduler would skip it forever (#24 follow-up)."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(nas, "any.gcode", marker="x")
        lib = build_external_library(db_session, nas, name="nas")

        def boom(_root):  # the walk itself fails, outside any per-file try/except
            raise OSError("transport endpoint is not connected")

        monkeypatch.setattr(external_library, "_walk", boom)

        summary = external_library.scan_library(lib.id)

        assert summary["aborted"] is True
        assert "scan_failed" in summary["error"]
        db_session.refresh(lib)
        assert lib.last_scan_status == ExternalLibraryScanStatus.ERROR
        # last_scanned_at is stamped so the scheduler treats it as due again (a
        # terminal state), rather than a permanently-skipped RUNNING row.
        assert lib.last_scanned_at is not None
