"""Remote discovery is paged, restart-safe, and never infers deletion early."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    ExternalLibrary,
    ExternalLibraryTombstone,
    File,
    LibrarySourceKind,
)
from app.services import external_library, trash
from app.services.library_source import SourceEntry, SourcePage
from tests.factories import build_file, build_model
from tests.paths import FIXTURES_DIR


class _Source:
    def __init__(self, page: SourcePage, path: Path | None = None) -> None:
        self.page = page
        self.path = path
        self.materialize_count = 0

    def list_page(self, _prefix: str, *, cursor: str | None, limit: int) -> SourcePage:
        del cursor
        assert limit == 1000
        return self.page

    @contextmanager
    def materialize(self, _key: str):
        assert self.path is not None
        self.materialize_count += 1
        yield self.path


def _remote_library(session: Session, name: str) -> ExternalLibrary:
    row = ExternalLibrary(
        name=name,
        root_path="",
        source_kind=LibrarySourceKind.S3,
        source_prefix="models",
        watch_mode="off",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _indexed(session: Session, library: ExternalLibrary, key: str) -> File:
    model = build_model(session, name=f"model-{key}")
    return build_file(
        session,
        model,
        filename=Path(key).name,
        external=True,
        external_library_id=library.id,
        source_key=key,
        path=f"source://1/{key}",
    )


class TestRemoteScanSafety:
    def test_missing_or_mounted_library_is_never_scanned_as_remote(
        self, db_session: Session
    ) -> None:
        with pytest.raises(ValueError, match="not found"):
            external_library.scan_remote_library(999999)

        mounted = ExternalLibrary(
            name="Mounted only",
            root_path="/mnt/models",
            source_kind=LibrarySourceKind.MOUNTED,
        )
        db_session.add(mounted)
        db_session.commit()
        db_session.refresh(mounted)
        with pytest.raises(ValueError, match="library_source_is_mounted"):
            external_library.scan_remote_library(mounted.id)

    def test_global_remote_scan_lock_coalesces_competing_work(
        self, db_session: Session
    ) -> None:
        library = _remote_library(db_session, "remote-coalesced")
        assert external_library._REMOTE_SCAN_LOCK.acquire(blocking=False)  # noqa: SLF001
        try:
            assert external_library.scan_remote_library(library.id) == {
                "coalesced": True,
                "reason": "remote_scan_busy",
            }
        finally:
            external_library._REMOTE_SCAN_LOCK.release()  # noqa: SLF001

    def test_wall_clock_slice_stops_between_objects_without_provider_backoff(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        library = _remote_library(db_session, "remote-deadline")
        source = _Source(
            SourcePage(
                (SourceEntry("models/later.gcode", 12),),
                "page-2",
                False,
                metadata_ops=1,
            )
        )
        ticks = iter((0.0, 0.0, 901.0))
        monkeypatch.setattr(external_library, "monotonic", lambda: next(ticks))
        monkeypatch.setattr(external_library, "source_for_library", lambda _lib: source)

        result = external_library.scan_remote_library(library.id)

        db_session.refresh(library)
        assert result["error"] == "remote_scan_slice_deadline"
        assert library.last_scan_status == "partial"
        checkpoint = db_session.exec(
            select(external_library.ExternalLibraryCheckpoint).where(
                external_library.ExternalLibraryCheckpoint.library_id == library.id
            )
        ).one()
        assert checkpoint.backoff_until is None

    def test_provider_failure_uses_job_aware_24_hour_backoff(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        library = _remote_library(db_session, "remote-provider-failure")

        class FailingSource:
            def list_page(self, *_args, **_kwargs):
                raise RuntimeError("provider unavailable")

        updates: list[tuple[str, dict[str, object]]] = []
        monkeypatch.setattr(
            external_library, "source_for_library", lambda _lib: FailingSource()
        )
        monkeypatch.setattr(
            external_library.registry,
            "update",
            lambda job_id, **values: updates.append((job_id, values)),
        )

        result = external_library.scan_remote_library(library.id, job_id="scan-job")

        checkpoint = db_session.exec(
            select(external_library.ExternalLibraryCheckpoint).where(
                external_library.ExternalLibraryCheckpoint.library_id == library.id
            )
        ).one()
        assert result["error"] == "provider unavailable"
        assert checkpoint.backoff_until is not None
        assert updates[-1] == (
            "scan-job",
            {"state": "failed", "error": "provider unavailable"},
        )

        blocked = external_library.scan_remote_library(library.id)
        assert blocked["backoff_until"] == checkpoint.backoff_until.isoformat()

    def test_page_larger_than_network_slice_is_rejected_before_download(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        library = _remote_library(db_session, "remote-byte-limit")
        source = _Source(
            SourcePage(
                (SourceEntry("models/large.gcode", 11),),
                None,
                True,
                metadata_ops=1,
            )
        )
        monkeypatch.setattr(external_library, "_REMOTE_SLICE_MAX_BYTES", 10)
        monkeypatch.setattr(external_library, "source_for_library", lambda _lib: source)

        result = external_library.scan_remote_library(library.id)

        assert result["error"] == "remote_scan_slice_byte_limit"
        assert source.materialize_count == 0

    def test_indexes_one_remote_artifact_in_place(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        library = _remote_library(db_session, "remote-import")
        path = FIXTURES_DIR / "sample.gcode"
        key = "models/sample.gcode"
        source = _Source(
            SourcePage(
                (SourceEntry(key, path.stat().st_size),),
                None,
                True,
                metadata_ops=1,
            ),
            path,
        )
        monkeypatch.setattr(external_library, "source_for_library", lambda _lib: source)

        result = external_library.scan_remote_library(library.id)

        row = db_session.exec(
            select(File).where(File.external_library_id == library.id)
        ).one()
        assert result["added"] == 1
        assert row.source_key == key
        assert row.path.endswith(f"/{key}")

    def test_completed_job_reports_bounded_scan_accounting(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        library = _remote_library(db_session, "remote-completed-job")
        source = _Source(SourcePage((), None, True, metadata_ops=2))
        updates: list[tuple[str, dict[str, object]]] = []
        monkeypatch.setattr(external_library, "source_for_library", lambda _lib: source)
        monkeypatch.setattr(
            external_library.registry,
            "update",
            lambda job_id, **values: updates.append((job_id, values)),
        )

        result = external_library.scan_remote_library(library.id, job_id="completed-job")

        assert result["complete"] is True
        assert updates[-1][0] == "completed-job"
        assert updates[-1][1]["state"] == "completed"

    def test_incomplete_epoch_never_marks_unseen_catalog_rows_absent(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        library = _remote_library(db_session, "remote-incomplete")
        existing = _indexed(db_session, library, "models/existing.gcode")
        source = _Source(
            SourcePage(
                entries=(SourceEntry("models/readme.txt", 10),),
                next_cursor="page-2",
                complete=False,
                metadata_ops=1,
            )
        )
        monkeypatch.setattr(external_library, "source_for_library", lambda _lib: source)

        result = external_library.scan_remote_library(library.id)

        db_session.refresh(existing)
        assert result["complete"] is False
        assert existing.deleted_at is None

    def test_empty_complete_epoch_is_a_mass_removal_error_not_authorization(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        library = _remote_library(db_session, "remote-empty")
        existing = _indexed(db_session, library, "models/precious.gcode")
        source = _Source(SourcePage((), None, True, metadata_ops=1))
        monkeypatch.setattr(external_library, "source_for_library", lambda _lib: source)

        result = external_library.scan_remote_library(library.id)

        db_session.refresh(existing)
        assert result["error"] == "remote_mass_removal_blocked"
        assert existing.deleted_at is None

    def test_tombstone_suppresses_reimport_until_explicitly_cleared(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        library = _remote_library(db_session, "remote-tombstone")
        key = "models/deleted.gcode"
        db_session.add(
            ExternalLibraryTombstone(
                library_id=library.id, source_key=key, reason="user_trashed"
            )
        )
        db_session.commit()
        source = _Source(
            SourcePage((SourceEntry(key, 12),), None, True, metadata_ops=1)
        )
        monkeypatch.setattr(external_library, "source_for_library", lambda _lib: source)

        result = external_library.scan_remote_library(library.id)

        rows = db_session.exec(
            select(File).where(File.external_library_id == library.id)
        ).all()
        assert result["skipped"] == 1
        assert rows == []

    def test_seven_day_hash_sweep_detects_same_size_same_mtime_change(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        library = _remote_library(db_session, "remote-hash-sweep")
        key = "models/stealth.gcode"
        before = b"G1 X1\n"
        after = b"G1 X2\n"
        source_path = tmp_path / "stealth.gcode"
        source_path.write_bytes(after)
        existing = _indexed(db_session, library, key)
        existing.sha256 = hashlib.sha256(before).hexdigest()
        existing.size_bytes = len(before)
        existing.source_mtime = 0.0
        existing.source_verified_at = utcnow() - timedelta(days=8)
        db_session.add(existing)
        db_session.commit()
        source = _Source(
            SourcePage((SourceEntry(key, len(after)),), None, True, metadata_ops=1),
            source_path,
        )
        monkeypatch.setattr(external_library, "source_for_library", lambda _lib: source)

        result = external_library.scan_remote_library(library.id)

        db_session.refresh(existing)
        assert result.get("error") is None, result
        assert result["updated"] == 1, result
        assert existing.sha256 == hashlib.sha256(after).hexdigest()
        assert existing.source_verified_at is not None
        assert source.materialize_count == 1

    def test_restoring_one_revision_clears_its_discovery_tombstone(
        self, db_session: Session
    ) -> None:
        library = _remote_library(db_session, "remote-restore-tombstone")
        key = "models/restored.gcode"
        existing = _indexed(db_session, library, key)
        existing.deleted_at = utcnow()
        db_session.add(existing)
        trash.record_source_tombstone(db_session, existing, "revision_trashed")
        db_session.commit()

        trash.restore_resource(db_session, existing)

        tombstone = db_session.exec(
            select(ExternalLibraryTombstone).where(
                ExternalLibraryTombstone.library_id == library.id,
                ExternalLibraryTombstone.source_key == key,
            )
        ).one()
        assert tombstone.cleared_at is not None
