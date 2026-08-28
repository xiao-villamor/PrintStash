"""`scan_library` on a folder it has already indexed.

The first scan is easy; every scan after it is the feature. A share is rescanned
on a schedule, and the reconcile step has to answer "what changed?" from nothing
but size and mtime, because hashing a multi-gigabyte NAS on every pass is not an
option. That trade-off is exactly where this can go wrong in both directions: too
eager and every scheduled scan rehashes an unchanged library over the network;
too lazy and edited files keep serving stale metadata forever.

So these tests pin the decision boundary. An unchanged file is skipped, an mtime
touched without a content change is still skipped (editors and rsync do this
constantly), sub-tolerance mtime jitter is skipped (network filesystems round
timestamps differently than local ones), and changed content reindexes the *same*
row rather than adding a duplicate. A file moved inside the share reads as
remove-then-add, which is the one case where the index legitimately churns — and
a file that is gone soft-deletes rather than vanishing, so a share that briefly
unmounts is recoverable."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import (
    File,
    Model,
)
from app.services import external_library
from tests._env import use_local_storage
from tests.factories import build_external_library
from tests.integration.services.external_library._helpers import (
    drop_gcode,
    enable_feature,
    external_files,
)


class TestScanLibrary:
    def test_rescan_is_idempotent(self, tmp_path: Path, db_session: Session) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        drop_gcode(nas, "a.gcode")
        lib = build_external_library(db_session, nas, name="nas")

        external_library.scan_library(lib.id)
        summary = external_library.scan_library(lib.id)

        assert summary["added"] == 0
        assert summary["skipped"] == 1
        assert summary["updated"] == 0

    def test_mtime_touch_without_content_change_is_skipped(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        """A backup tool that rewrites mtimes but not bytes must not trigger a
        needless re-import — we just record the new signature."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        path = drop_gcode(nas, "stable.gcode", marker="touch")
        lib = build_external_library(db_session, nas, name="nas")
        external_library.scan_library(lib.id)
        before = external_files(db_session)[0]
        old_sha = before.sha256

        future = path.stat().st_mtime + 5000.0
        os.utime(path, (future, future))

        summary = external_library.scan_library(lib.id)

        assert summary["updated"] == 0
        assert summary["skipped"] == 1
        db_session.expire_all()  # scan committed via its own session
        after = db_session.get(File, before.id)
        assert after.sha256 == old_sha  # content unchanged
        assert after.source_mtime == pytest.approx(future)  # signature refreshed

    def test_mtime_jitter_within_tolerance_skips_rehash(
        self, tmp_path: Path, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sub-second/FAT-granularity mtime drift on unchanged content takes the
        cheap skip path — it must not trigger a full sha256 re-hash over the NAS."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        path = drop_gcode(nas, "stable.gcode", marker="jitter")
        lib = build_external_library(db_session, nas, name="nas")
        external_library.scan_library(lib.id)

        # Nudge mtime by less than the tolerance (content identical).
        jittered = path.stat().st_mtime + 1.0
        os.utime(path, (jittered, jittered))

        calls = {"n": 0}
        real_hash = external_library.sha256_file

        def counting_hash(p):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return real_hash(p)

        monkeypatch.setattr(external_library, "sha256_file", counting_hash)

        summary = external_library.scan_library(lib.id)

        assert summary["skipped"] == 1
        assert summary["updated"] == 0
        assert calls["n"] == 0  # no re-hash storm for jitter under the tolerance

    def test_changed_content_reindexes_same_row(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        path = drop_gcode(nas, "a.gcode")
        lib = build_external_library(db_session, nas, name="nas")

        external_library.scan_library(lib.id)
        before = db_session.exec(select(File).where(File.is_external == True)).all()  # noqa: E712
        assert len(before) == 1
        old_sha = before[0].sha256

        # Append bytes so size + content differ.
        with path.open("ab") as fh:
            fh.write(b"\n; mutated\nG1 X1 Y1\n")

        summary = external_library.scan_library(lib.id)

        assert summary["updated"] == 1
        assert summary["added"] == 0
        after = db_session.exec(select(File).where(File.is_external == True)).all()  # noqa: E712
        assert len(after) == 1  # same row, no duplicate / new version
        db_session.refresh(after[0])
        assert after[0].sha256 != old_sha

    def test_file_moved_within_nas_is_reconciled(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        """Moving a file to another subfolder reads as remove(old) + add(new): the
        index follows the file to its new path without leaving a stale live row."""
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        old_path = drop_gcode(nas / "incoming", "widget.gcode", marker="move")
        lib = build_external_library(db_session, nas, name="nas")
        external_library.scan_library(lib.id)
        assert len(external_files(db_session)) == 1

        new_dir = nas / "sorted"
        new_dir.mkdir(parents=True)
        shutil.move(str(old_path), str(new_dir / "widget.gcode"))

        summary = external_library.scan_library(lib.id)

        assert summary["added"] == 1
        assert summary["removed"] == 1
        live_files = external_files(db_session)
        assert len(live_files) == 1
        assert live_files[0].path == str(new_dir / "widget.gcode")

    def test_a_removed_file_trashes_the_model_it_was_alone_in(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        enable_feature(db_session)
        nas = tmp_path / "nas"
        path = drop_gcode(nas, "gone.gcode")
        # A distinct-content file so the folder stays non-empty (not an "unmount")
        # and dedup keeps it under a *different* model than gone.gcode.
        stays = drop_gcode(nas, "stays.gcode")
        with stays.open("ab") as fh:
            fh.write(b"\n; distinct content\nG1 X5 Y5\n")
        lib = build_external_library(db_session, nas, name="nas")
        external_library.scan_library(lib.id)

        f = db_session.exec(
            select(File).where(File.original_filename == "gone.gcode")
        ).first()
        model_id = f.model_id
        path.unlink()

        summary = external_library.scan_library(lib.id)

        assert summary["removed"] == 1
        db_session.expire_all()
        f2 = db_session.get(File, f.id)
        assert f2.deleted_at is not None  # soft-deleted, not hard-deleted
        model = db_session.get(Model, model_id)
        assert model.deleted_at is not None
