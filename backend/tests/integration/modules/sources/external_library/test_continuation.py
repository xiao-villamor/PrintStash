"""A bounded remote scan leaves durable continuation intent, without reviving cancellations."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlmodel import select

from app.db.models import (
    ExternalLibraryCheckpoint,
    File,
    JobKind,
    JobState,
    LibrarySourceKind,
)
from app.modules.sources import external_library
from app.modules.sources.library_source import (
    LibrarySourceError,
    SourceContent,
    SourceEntry,
    SourcePage,
)
from app.modules.work.jobs import jobs
from tests.factories.ops import build_job, build_job_context
from tests.paths import FIXTURES_DIR


@pytest.fixture
def library(make_external_library):
    return make_external_library(
        "", source_kind=LibrarySourceKind.S3, source_prefix="models", scan_schedule=""
    )


@pytest.fixture
def source(monkeypatch):
    path = FIXTURES_DIR / "sample.gcode"

    class Source:
        error = None

        def list_page(self, _prefix, *, cursor, limit):
            if self.error is not None:
                raise self.error
            if cursor is None:
                return SourcePage(
                    (SourceEntry("models/first.gcode", path.stat().st_size),),
                    "second-page",
                    False,
                    1,
                    entry_cursors=("second-page",),
                )
            assert cursor == "second-page"
            return SourcePage(
                (SourceEntry("models/second.gcode", path.stat().st_size),),
                None,
                True,
                1,
                entry_cursors=("end",),
            )

        @contextmanager
        def materialize(self, key, *, expected=None):
            yield SourceContent(path, SourceEntry(key, path.stat().st_size))

    result = Source()
    monkeypatch.setattr(external_library, "source_for_library", lambda _lib: result)
    return result


class TestRemoteScanContinuation:
    def test_finishes_multiple_pages_from_one_request(
        self, db_session, library, source
    ):
        external_library.request_scan(db_session, library.id)
        build_job(
            db_session,
            kind=JobKind.SOURCES_SCAN,
            id="first",
            subject=f"library/{library.id}",
        )
        external_library._scan_step(build_job_context("first"))

        db_session.expire_all()
        assert library.id in external_library.libraries_due_for_scan(db_session)
        assert jobs.get("first").state is JobState.COMPLETED
        checkpoint = db_session.exec(select(ExternalLibraryCheckpoint)).one()
        assert checkpoint.cursor == "second-page"
        epoch = checkpoint.epoch

        build_job(
            db_session,
            kind=JobKind.SOURCES_SCAN,
            id="second",
            subject=f"library/{library.id}",
        )
        external_library._scan_step(build_job_context("second"))

        db_session.expire_all()
        assert jobs.get("second").state is JobState.COMPLETED
        assert external_library.libraries_due_for_scan(db_session) == []
        checkpoint = db_session.exec(select(ExternalLibraryCheckpoint)).one()
        assert checkpoint.complete is True
        assert checkpoint.epoch == epoch
        assert {
            row.source_key
            for row in db_session.exec(
                select(File).where(File.external_library_id == library.id)
            ).all()
            if row.deleted_at is None
        } == {
            "models/first.gcode",
            "models/second.gcode",
        }

    def test_deadline_leaves_successful_slice_for_reconciliation(
        self, db_session, library, source
    ):
        error = LibrarySourceError("remote_scan_slice_deadline")
        error.discovery_cursor = "second-page"
        source.error = error
        external_library.request_scan(db_session, library.id)
        build_job(
            db_session,
            kind=JobKind.SOURCES_SCAN,
            id="deadline",
            subject=f"library/{library.id}",
        )
        external_library._scan_step(build_job_context("deadline"))

        db_session.expire_all()
        assert jobs.get("deadline").state is JobState.COMPLETED
        assert library.id in external_library.libraries_due_for_scan(db_session)
        assert library.last_scan_status == "partial"
        checkpoint = db_session.exec(select(ExternalLibraryCheckpoint)).one()
        assert checkpoint.cursor == "second-page"
        assert checkpoint.backoff_until is None
        source.error = None
        build_job(
            db_session,
            kind=JobKind.SOURCES_SCAN,
            id="resumed",
            subject=f"library/{library.id}",
        )
        external_library._scan_step(build_job_context("resumed"))
        db_session.expire_all()
        assert db_session.exec(select(ExternalLibraryCheckpoint)).one().complete is True
        assert external_library.libraries_due_for_scan(db_session) == []

    def test_cancellation_does_not_requeue(self, db_session, library, source):
        source.error = asyncio.CancelledError()
        external_library.request_scan(db_session, library.id)
        build_job(
            db_session,
            kind=JobKind.SOURCES_SCAN,
            id="cancelled",
            subject=f"library/{library.id}",
        )
        with pytest.raises(asyncio.CancelledError):
            external_library._scan_step(build_job_context("cancelled"))
        db_session.expire_all()
        assert library.scan_requested_at is None
        assert external_library.libraries_due_for_scan(db_session) == []
        assert (
            db_session.exec(select(ExternalLibraryCheckpoint)).one().complete is False
        )

    def test_provider_failure_does_not_requeue(self, db_session, library, source):
        source.error = LibrarySourceError("provider_unavailable")
        external_library.request_scan(db_session, library.id)
        build_job(
            db_session,
            kind=JobKind.SOURCES_SCAN,
            id="failed",
            subject=f"library/{library.id}",
        )
        external_library._scan_step(build_job_context("failed"))
        db_session.expire_all()
        assert jobs.get("failed").state is JobState.FAILED
        assert library.scan_requested_at is None
        assert external_library.libraries_due_for_scan(db_session) == []
        assert (
            db_session.exec(select(ExternalLibraryCheckpoint)).one().backoff_until
            is not None
        )

    def test_cancel_requested_continuation_stays_cancelled(
        self, db_session, library, source
    ):
        build_job(
            db_session,
            kind=JobKind.SOURCES_SCAN,
            id="paged",
            subject=f"library/{library.id}",
        )
        external_library._scan_step(build_job_context("paged"))
        db_session.expire_all()
        assert library.id in external_library.libraries_due_for_scan(db_session)
        external_library._cancel_scan(db_session, f"library/{library.id}")
        db_session.commit()
        assert external_library.libraries_due_for_scan(db_session) == []
