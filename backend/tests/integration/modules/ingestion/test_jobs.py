"""The ``ingest.*`` Jobs: accepted imports, one Job per request.

Every ingest Job is requested: the route records the request, its staged bytes
and the queued Job together, so the Job row is its own pending marker. What
these definitions add is what happens to the intent. Cancelling releases the
staged bytes at once and forgets any stored credential, so a cancelled import
leaves nothing behind. A retry is possible only while what it needs remains:
the request, and for an upload or archive its staged bytes, whose lease is
renewed so staging cleanup does not take them mid-retry. An import already
claimed by a Model is not imported twice. The steps themselves run through the
ingest routes' suites; here, a Job whose staging expired fails saying so.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.time import ensure_utc, utcnow
from app.db.models import (
    IngestRequest,
    IngestRequestKind,
    Job,
    JobKind,
    JobState,
    LaneName,
    StagingLease,
)
from app.modules.ingestion import jobs as ingest_jobs
from app.modules.ingestion.requests import subject_key
from app.modules.work.submission import submit

DEFINITIONS = {definition.name: definition for definition in ingest_jobs.definitions()}
UPLOAD = DEFINITIONS[JobKind.INGESTION_UPLOAD]


@pytest.fixture
def owner(make_user):
    return make_user()


def _stage(session: Session, request: IngestRequest, tmp_path: Path, **fields) -> Path:
    path = tmp_path / f"{request.job_id}.stl"
    path.write_bytes(b"staged")
    from app.modules.ingestion.staging_leases import create_job_lease

    lease = create_job_lease(
        session,
        job_id=request.job_id,
        owner_user_id=request.owner_user_id,
        path=path,
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    lease.expires_at = utcnow() + timedelta(minutes=5)
    for name, value in fields.items():
        setattr(lease, name, value)
    session.add(lease)
    session.commit()
    return path


def _leases(session: Session, job_id: str) -> list[StagingLease]:
    session.expire_all()
    return list(
        session.exec(select(StagingLease).where(StagingLease.job_id == job_id)).all()
    )


class TestDefinitions:
    @pytest.mark.parametrize(
        ("name", "lane"),
        [
            (JobKind.INGESTION_UPLOAD, LaneName.INGEST),
            (JobKind.INGESTION_ARCHIVE_INSPECT, LaneName.INGEST),
            (JobKind.INGESTION_ARCHIVE_SELECTION, LaneName.INGEST),
            (JobKind.INGESTION_URL, LaneName.NETWORK),
            (JobKind.INGESTION_URL_SELECTION, LaneName.NETWORK),
            (JobKind.INGESTION_COLLECTION, LaneName.NETWORK),
        ],
    )
    def test_each_import_runs_in_the_lane_its_io_needs(
        self, name: JobKind, lane: LaneName
    ) -> None:
        # Network-bound imports must not queue behind local hashing.
        assert DEFINITIONS[name].lane == lane

    def test_every_import_is_requested_not_discovered(self) -> None:
        assert all(
            definition.source is None
            for name, definition in DEFINITIONS.items()
            if name is not JobKind.INGESTION_SCRATCH_CLEANUP
        )


class TestCancel:
    def test_releases_the_staged_bytes(
        self, db_session: Session, owner, make_ingest_request, tmp_path
    ) -> None:
        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        staged = _stage(db_session, request, tmp_path)

        UPLOAD.cancel(db_session, subject_key(request.job_id))
        db_session.commit()

        assert not staged.exists()
        assert _leases(db_session, request.job_id) == []

    def test_cancellation_keeps_uncertain_ownership(
        self, db_session, owner, make_ingest_request, tmp_path
    ):
        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        staged = _stage(db_session, request, tmp_path)
        staged.unlink()
        staged.write_bytes(b"replacement")
        UPLOAD.cancel(db_session, subject_key(request.job_id))
        db_session.commit()
        assert staged.read_bytes() == b"replacement"
        assert len(_leases(db_session, request.job_id)) == 1

    def test_forgets_a_stored_credential(
        self, db_session: Session, owner, make_ingest_request
    ) -> None:
        request = make_ingest_request(owner, source_credential="thingiverse-cookie")

        DEFINITIONS[JobKind.INGESTION_URL].cancel(
            db_session, subject_key(request.job_id)
        )
        db_session.commit()

        db_session.refresh(request)
        assert request.source_credential is None

    def test_a_request_already_gone_is_nothing_to_cancel(
        self, db_session: Session
    ) -> None:
        UPLOAD.cancel(db_session, subject_key("ingest-missing"))


class TestRetry:
    def test_an_upload_with_its_bytes_can_be_retried(
        self, db_session: Session, owner, make_ingest_request, tmp_path
    ) -> None:
        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        _stage(db_session, request, tmp_path)

        assert UPLOAD.retry(db_session, subject_key(request.job_id)) is True

    def test_replaced_input_cannot_be_retried(
        self, db_session, owner, make_ingest_request, tmp_path
    ):
        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        staged = _stage(db_session, request, tmp_path)
        staged.unlink()
        staged.write_bytes(b"replacement")
        assert UPLOAD.retry(db_session, subject_key(request.job_id)) is False
        assert staged.read_bytes() == b"replacement"

    def test_a_retry_renews_the_staging_lease(
        self, db_session: Session, owner, make_ingest_request, tmp_path
    ) -> None:
        # Staging cleanup reclaims expired leases; the retried import needs them.
        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        _stage(db_session, request, tmp_path)
        before = ensure_utc(_leases(db_session, request.job_id)[0].expires_at)

        UPLOAD.retry(db_session, subject_key(request.job_id))
        db_session.commit()

        assert ensure_utc(_leases(db_session, request.job_id)[0].expires_at) > before

    @pytest.mark.parametrize(
        "kind",
        [
            IngestRequestKind.UPLOAD,
            IngestRequestKind.ARCHIVE_INSPECT,
            IngestRequestKind.ARCHIVE_SELECTION,
        ],
    )
    def test_staged_bytes_that_are_gone_cannot_be_retried(
        self, db_session: Session, owner, make_ingest_request, tmp_path, kind
    ) -> None:
        request = make_ingest_request(owner, kind=kind)
        _stage(db_session, request, tmp_path).unlink()

        assert UPLOAD.retry(db_session, subject_key(request.job_id)) is False

    def test_an_upload_without_a_lease_cannot_be_retried(
        self, db_session: Session, owner, make_ingest_request
    ) -> None:
        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)

        assert UPLOAD.retry(db_session, subject_key(request.job_id)) is False

    def test_a_url_import_needs_no_staged_bytes(
        self, db_session: Session, owner, make_ingest_request
    ) -> None:
        request = make_ingest_request(owner, kind=IngestRequestKind.URL)

        assert (
            DEFINITIONS[JobKind.INGESTION_URL].retry(
                db_session, subject_key(request.job_id)
            )
            is True
        )

    def test_an_import_a_model_already_claimed_is_not_retried(
        self, db_session: Session, owner, make_ingest_request
    ) -> None:
        request = make_ingest_request(
            owner, kind=IngestRequestKind.URL, manifest_json=json.dumps({"claimed": 7})
        )

        assert (
            DEFINITIONS[JobKind.INGESTION_URL].retry(
                db_session, subject_key(request.job_id)
            )
            is False
        )

    def test_a_request_that_is_gone_cannot_be_retried(
        self, db_session: Session
    ) -> None:
        assert UPLOAD.retry(db_session, subject_key("ingest-missing")) is False


class TestUploadJob:
    def test_an_upload_whose_staging_expired_fails_saying_so(
        self, db_session: Session, work_engine, owner, make_ingest_request
    ) -> None:
        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)

        submit(request.job_id)
        work_engine.run_one()

        db_session.expire_all()
        job = db_session.get(Job, request.job_id)
        assert job is not None and job.state == JobState.FAILED
        assert "staging_expired" in json.loads(job.status_json).get("error", "")


class TestDbosBatchWithdrawal:
    def test_cancellation_stops_the_batch_after_a_committed_artifact(
        self, tmp_path, monkeypatch
    ) -> None:
        import threading
        from dataclasses import replace

        from app.db.models import File
        from app.db.session import get_session_factory
        from app.modules.ingestion import importer
        from app.modules.storage.storage_backend.runtime import get_backend
        from app.modules.work import catalog as catalog_module
        from app.modules.work import service
        from app.modules.work.catalog import WorkCatalog
        from app.modules.work.contracts import Step
        from tests.contract.modules.work._harness import DbosHarness, shared_app_db
        from tests.factories import build_ingest_request, build_user
        from tests.factories.content import gcode

        previous_engine = catalog_module.get_engine()
        previous_catalog = catalog_module.get_catalog()
        first = tmp_path / "first.gcode"
        second = tmp_path / "second.gcode"
        body = gcode(marker="dbos-committed-first")
        first.write_bytes(body)
        second.write_bytes(gcode(marker="dbos-not-imported"))
        finished = threading.Event()
        committed = []
        original = importer._ingest_one_file

        with shared_app_db(tmp_path / "application.sqlite"):
            with get_session_factory().scoped_session() as session:
                owner = build_user(session, "dbos-import-owner")
                request = build_ingest_request(
                    session, owner, kind=IngestRequestKind.COLLECTION
                )
                session.commit()
                owner_id, job_id = owner.id, request.job_id

            def ingest_step(context):
                try:
                    importer.import_assets(
                        job_context=context,
                        staged_files=[(first, first.name), (second, second.name)],
                        collection=None,
                        tags=None,
                        source_url=None,
                        actor_user_id=owner_id,
                        session_factory=get_session_factory(),
                    )
                finally:
                    finished.set()

            def commit_then_cancel(*args, **kwargs):
                outcome = original(*args, **kwargs)
                assert outcome is not None and "file_id" in outcome, outcome
                committed.append(outcome["file_id"])
                with get_session_factory().scoped_session() as session:
                    file = session.get(File, outcome["file_id"])
                    assert file is not None
                    assert get_backend().read_bytes(file.path) == body
                service.cancel(job_id, actor=owner)
                return outcome

            monkeypatch.setattr(importer, "_ingest_one_file", commit_then_cancel)
            definition = previous_catalog.definition(JobKind.INGESTION_COLLECTION)
            scoped_catalog = WorkCatalog(
                [
                    replace(
                        candidate,
                        steps=(Step("ingest.collection.cancel-probe", ingest_step),),
                    )
                    if candidate.name is definition.name
                    else candidate
                    for candidate in previous_catalog.definitions.values()
                ],
                lanes=previous_catalog.lanes,
            )
            system_db_path = tmp_path / "engine.sqlite"
            harness = DbosHarness(
                scoped_catalog,
                f"sqlite:///{system_db_path}",
                app_version="integration-batch-cancellation",
                listen_lanes=[definition.lane],
            )
            catalog_module.bind(harness.engine, scoped_catalog)
            try:
                submit(job_id)
                harness.wait_for(finished.is_set, timeout=10)
                with get_session_factory().scoped_session() as session:
                    job = session.get(Job, job_id)
                    assert job is not None and job.state is JobState.CANCELLED
                    files = session.exec(select(File)).all()
                    assert len(committed) == 1
                    assert [file.id for file in files] == committed
                    assert get_backend().read_bytes(files[0].path) == body
                assert not first.exists()
                assert not second.exists()
            finally:
                harness.close()
                catalog_module.bind(previous_engine, previous_catalog)


class TestScratchCleanupSource:
    def test_discovers_due_windows_in_bounded_order(self, db_session, local_storage):
        from tests.factories.ingestion_scratch import build_ingestion_scratch_window

        now = utcnow()
        first = build_ingestion_scratch_window(
            db_session, available_at=now - timedelta(minutes=2)
        )
        build_ingestion_scratch_window(
            db_session, available_at=now + timedelta(minutes=2)
        )
        build_ingestion_scratch_window(
            db_session, available_at=now - timedelta(minutes=1)
        )

        items = ingest_jobs.ScratchCleanupSource().pending(db_session, now=now, limit=1)

        assert [item.subject_key for item in items] == [f"scratch_window/{first.id}"]
        due = ingest_jobs.ScratchCleanupSource().next_due(db_session, now=now)
        assert due is not None
        assert ensure_utc(due) == ensure_utc(first.available_at)

    def test_reclaims_abandoned_window_through_definition(
        self, local_storage, make_job
    ):
        from app.db.models import CapacityReservation, IngestionScratchWindow
        from app.db.session import get_session_factory
        from app.modules.ingestion import scratch_windows
        from tests.factories.ops import build_job_context

        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
        )
        (window.directory / "partial").write_bytes(b"owned")
        window.detach()
        job = make_job(
            kind=JobKind.INGESTION_SCRATCH_CLEANUP,
            subject=f"scratch_window/{window.id}",
        )
        definition = DEFINITIONS[JobKind.INGESTION_SCRATCH_CLEANUP]

        definition.steps[0].fn(build_job_context(job.id))

        assert not window.directory.exists()
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, window.id) is None
            assert session.get(CapacityReservation, window.operation_id) is None

    def test_delays_recovery_of_an_active_writer(self, local_storage, make_job):
        from app.db.models import CapacityReservation, IngestionScratchWindow
        from app.db.session import get_session_factory
        from app.modules.ingestion import scratch_windows
        from tests.factories.ops import build_job_context

        before = utcnow()
        with scratch_windows.open_window(
            kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
        ) as window:
            output = window.directory / "partial"
            output.write_bytes(b"owned")
            job = make_job(
                kind=JobKind.INGESTION_SCRATCH_CLEANUP,
                subject=f"scratch_window/{window.id}",
            )

            DEFINITIONS[JobKind.INGESTION_SCRATCH_CLEANUP].steps[0].fn(
                build_job_context(job.id)
            )

            assert output.read_bytes() == b"owned"
            with get_session_factory().scoped_session() as session:
                row = session.get(IngestionScratchWindow, window.id)
                assert row is not None
                assert ensure_utc(row.available_at) >= ensure_utc(
                    before + timedelta(seconds=60)
                )
                assert session.get(CapacityReservation, window.operation_id) is not None

    def test_preserves_replacement_during_background_recovery(
        self, local_storage, make_job
    ):
        from app.db.models import IngestionScratchWindow
        from app.db.session import get_session_factory
        from app.modules.ingestion import scratch_windows
        from tests.factories.ops import build_job_context

        window = scratch_windows.create_window(
            kind=scratch_windows.WindowKind.LOCAL_COPY, max_bytes=16
        )
        window.detach()
        window.directory.rename(window.directory.with_name(window.id + "-displaced"))
        window.directory.mkdir()
        replacement = window.directory / "foreign"
        replacement.write_bytes(b"foreign")
        job = make_job(
            kind=JobKind.INGESTION_SCRATCH_CLEANUP,
            subject=f"scratch_window/{window.id}",
        )

        DEFINITIONS[JobKind.INGESTION_SCRATCH_CLEANUP].steps[0].fn(
            build_job_context(job.id)
        )

        assert replacement.read_bytes() == b"foreign"
        with get_session_factory().scoped_session() as session:
            assert session.get(IngestionScratchWindow, window.id) is not None


class TestJobInput:
    def test_cleanup_fault_preserves_primary_actor_error(
        self,
        db_session,
        owner,
        make_ingest_request,
        tmp_path,
        monkeypatch,
    ):
        from app.modules.work.jobs import jobs
        from tests.factories.ops import build_job_context

        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        path = _stage(db_session, request, tmp_path)
        context = build_job_context(request.job_id)
        primary = RuntimeError("actor failed during persistence")

        def fail_cleanup(*args, **kwargs):
            raise RuntimeError("settled replay SQL unavailable")

        monkeypatch.setattr(jobs, "reconcile_settled_attempt", fail_cleanup)

        with pytest.raises(
            RuntimeError, match="actor failed during persistence"
        ) as caught:
            with ingest_jobs._job_input(context):
                raise primary

        assert caught.value is primary
        assert any(
            "settled replay SQL unavailable" in note for note in primary.__notes__
        )
        assert path.read_bytes() == b"staged"

    @pytest.mark.parametrize("field", ["device", "inode", "ctime_ns", "size_bytes"])
    def test_input_rejects_invalid_receipt_identity(
        self,
        db_session,
        owner,
        make_ingest_request,
        tmp_path,
        field,
    ):
        from app.modules.ingestion import staging_leases

        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        path = _stage(db_session, request, tmp_path)
        lease = _leases(db_session, request.job_id)[0]
        setattr(lease, field, getattr(lease, field) + 1)
        db_session.add(lease)
        db_session.commit()

        with pytest.raises(staging_leases.StagingLeaseError, match="identity"):
            staging_leases.open_job_input(db_session, request.job_id)

        assert path.read_bytes() == b"staged"

    @pytest.mark.parametrize("replacement", ["regular", "symlink", "directory"])
    def test_input_rejects_replacement_after_receipt_read(
        self,
        db_session,
        owner,
        make_ingest_request,
        tmp_path,
        replacement,
    ):
        from app.modules.ingestion import staging_leases

        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        path = _stage(db_session, request, tmp_path)
        custody = staging_leases.open_job_input(db_session, request.job_id)
        outside = tmp_path / "foreign-file"
        outside.write_bytes(b"foreign bytes")
        path.unlink()
        replace = {
            "regular": lambda: path.write_bytes(b"foreign bytes"),
            "symlink": lambda: path.symlink_to(outside),
            "directory": lambda: path.mkdir(),
        }[replacement]
        replace()

        with pytest.raises(staging_leases.StagingLeaseError, match="custody"):
            with custody:
                pytest.fail("replacement acquired staged-input custody")

        assert path.exists()
        assert outside.read_bytes() == b"foreign bytes"

    def test_retry_actor_waits_for_previous_input_owner(
        self,
        db_session,
        owner,
        make_ingest_request,
        tmp_path,
        monkeypatch,
    ):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        from threading import Event

        from app.db.session import get_session_factory, override_session_factory
        from app.modules.work import service
        from tests.factories.ops import build_job_context

        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        path = _stage(db_session, request, tmp_path)
        old_context = build_job_context(request.job_id)
        factory = get_session_factory()
        entered = Event()
        monkeypatch.setattr(service, "nudge", lambda *_args: None)

        def retry_actor(context):
            override_session_factory(factory)
            entered.set()
            with ingest_jobs._job_input(context) as owned:
                return owned.read_bytes()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with ingest_jobs._job_input(old_context):
                service.cancel(request.job_id, actor=owner)
                service.retry(request.job_id, actor=owner)
                new_context = build_job_context(request.job_id)
                running = executor.submit(retry_actor, new_context)
                assert entered.wait(timeout=2)
                with pytest.raises(TimeoutError):
                    running.result(timeout=0.2)
                assert path.read_bytes() == b"staged"
            assert running.result(timeout=3) == b"staged"
        assert path.read_bytes() == b"staged"
        assert len(_leases(db_session, request.job_id)) == 1
