"""Staging recovery releases only proven bytes after work has settled."""

import hashlib
from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.db.models import IngestRequestKind, JobState, StagingLease
from app.modules.ingestion import staging_cleanup, staging_leases
from app.modules.ingestion.ingestion import release_job_staging
from tests.factories.ops import build_job_context


@pytest.fixture(autouse=True)
def _concurrent_connections(_patch_engine, tmp_path):
    """Independent WAL connections queue competing writers as production does."""
    from sqlalchemy import event
    from sqlmodel import SQLModel, create_engine

    from app.db.session import (
        SQLiteSessionFactory,
        _set_sqlite_pragmas,
        get_session_factory,
        override_session_factory,
    )

    engine = create_engine(
        f"sqlite:///{tmp_path / 'ownership.db'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    previous = get_session_factory()
    override_session_factory(SQLiteSessionFactory(engine))
    try:
        yield
    finally:
        override_session_factory(previous)
        engine.dispose()


@pytest.fixture
def staged_input(db_session, make_ingest_request, make_user, tmp_path):
    request = make_ingest_request(
        make_user(), kind=IngestRequestKind.UPLOAD, state=JobState.FAILED
    )
    path = tmp_path / "upload.stl"
    path.write_bytes(b"original retry input")
    lease = staging_leases.create_job_lease(
        db_session,
        job_id=request.job_id,
        owner_user_id=request.owner_user_id,
        path=path,
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    db_session.commit()
    return request, path, lease


class TestStagingCleanup:
    def test_release_preserves_a_replacement_file(self, staged_input, db_session):
        request, path, lease = staged_input
        path.unlink()
        path.write_bytes(b"replacement")
        release_job_staging(build_job_context(request.job_id))
        assert path.read_bytes() == b"replacement"
        db_session.expire_all()
        assert db_session.get(StagingLease, lease.id) is not None

    def test_reconciliation_retains_failed_retry_input(self, staged_input, db_session):
        request, path, lease = staged_input
        staging_cleanup.reconcile_jobs(db_session)
        db_session.commit()
        assert path.read_bytes() == b"original retry input"
        assert db_session.get(StagingLease, lease.id) is not None

    def test_reconciliation_releases_completed_upload_before_expiry(
        self, staged_input, db_session, make_file, make_model
    ):
        from app.db.models import Job

        request, path, lease = staged_input
        job = db_session.get(Job, request.job_id)
        make_file(make_model(), ingestion_key=request.job_id)
        job.state = JobState.COMPLETED
        db_session.add(job)
        db_session.commit()
        staging_cleanup.reconcile_jobs(db_session)
        db_session.commit()
        assert not path.exists()
        assert db_session.get(StagingLease, lease.id) is None

    def test_expiry_preserves_active_input(self, staged_input, db_session):
        from app.db.models import Job

        request, path, lease = staged_input
        job = db_session.get(Job, request.job_id)
        job.state = JobState.RUNNING
        lease.expires_at = utcnow() - timedelta(hours=1)
        db_session.add(job)
        db_session.add(lease)
        db_session.commit()
        staging_cleanup.prune_expired(db_session)
        db_session.commit()
        assert path.read_bytes() == b"original retry input"
        assert db_session.get(StagingLease, lease.id) is not None

    def test_discard_serializes_against_retry(
        self, staged_input, db_session, monkeypatch
    ):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from app.core.errors import OperationError
        from app.db.models import Job, User
        from app.modules.work import service

        request, path, lease = staged_input
        job_id, lease_id = request.job_id, lease.id
        actor = db_session.get(User, request.owner_user_id)
        _ = actor.id, actor.is_superuser
        db_session.expunge(actor)
        monkeypatch.setattr(service, "nudge", lambda *_args: None)
        from app.db.session import get_session_factory, override_session_factory

        factory = get_session_factory()
        barrier = Barrier(2)

        def attempt(action):
            override_session_factory(factory)
            barrier.wait(timeout=5)
            try:
                action(job_id, actor=actor)
                return "accepted"
            except OperationError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            discarded = executor.submit(attempt, staging_cleanup.discard)
            retried = executor.submit(attempt, service.retry)
            results = (discarded.result(timeout=20), retried.result(timeout=20))
        db_session.expire_all()
        job = db_session.get(Job, job_id)
        retained = db_session.get(StagingLease, lease_id)
        if job.state == JobState.QUEUED:
            assert results == ("staging_job_not_terminal", "accepted")
            assert retained is not None
            assert path.read_bytes() == b"original retry input"
        else:
            assert results == ("accepted", "job_subject_gone")
            assert retained is None
            assert not path.exists()


class TestCancellationCustody:
    def test_cancel_preserves_input_until_upload_actor_stops(
        self,
        db_session,
        make_user,
        make_ingest_request,
        local_storage,
        tmp_path,
        monkeypatch,
        caplog,
    ):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        from app.db.models import FileType, Job
        from app.db.session import get_session_factory, override_session_factory
        from app.modules.ingestion import ingestion
        from app.modules.work import catalog, runner, service
        from tests.factories.content import gcode

        actor = make_user()
        request = make_ingest_request(
            actor,
            kind=IngestRequestKind.UPLOAD,
            original_filename="cancel-race.gcode",
            file_type=FileType.GCODE,
            model_name="cancel race",
        )
        body = gcode(marker="cancel-running-upload")
        staged = tmp_path / "cancel-race.gcode"
        staged.write_bytes(body)
        lease = staging_leases.create_job_lease(
            db_session,
            job_id=request.job_id,
            owner_user_id=actor.id,
            path=staged,
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
        db_session.commit()
        context = build_job_context(request.job_id)
        definition = catalog.get_catalog().definition(context.definition)
        factory = get_session_factory()
        entered, resume = Event(), Event()
        reserve_version = ingestion._reserve_version_before_publication

        def reserve_then_pause(session, model):
            version = reserve_version(session, model)
            entered.set()
            assert resume.wait(timeout=10), "parent did not resume upload actor"
            return version

        def upload_actor():
            override_session_factory(factory)
            return runner._run_step(
                definition.steps[0],
                context,
                mutating=definition.mutating,
            )

        monkeypatch.setattr(
            ingestion, "_reserve_version_before_publication", reserve_then_pause
        )
        _ = actor.id, actor.is_superuser
        db_session.expunge(actor)

        with ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(upload_actor)
            try:
                assert entered.wait(timeout=10), "upload did not enter persistence"
                cancelled = service.cancel(request.job_id, actor=actor)

                assert cancelled.state == JobState.CANCELLED.value
                assert not running.done(), "actor must still be inside persistence"
                assert staged.read_bytes() == body
                with factory.scoped_session() as session:
                    job = session.get(Job, request.job_id)
                    assert job is not None and job.state == JobState.CANCELLED
                    assert session.get(StagingLease, lease.id) is not None
            finally:
                resume.set()
                running.result(timeout=15)
        assert not staged.exists()
        with factory.scoped_session() as session:
            assert session.get(StagingLease, lease.id) is None
            assert session.get(Job, request.job_id).state == JobState.CANCELLED
        assert not any(
            record.message == "ingestion commit failed" for record in caplog.records
        )

    def test_reconciliation_reclaims_cancelled_input_after_actor_sigkill(
        self,
        db_session,
        make_user,
        make_ingest_request,
        tmp_path,
    ):
        import os
        import selectors
        import signal
        import subprocess
        import sys

        actor = make_user()
        request = make_ingest_request(
            actor, kind=IngestRequestKind.UPLOAD, state=JobState.CANCELLED
        )
        body = b"cancelled actor durable input"
        staged = tmp_path / "cancelled-input.stl"
        staged.write_bytes(body)
        lease = staging_leases.create_job_lease(
            db_session,
            job_id=request.job_id,
            owner_user_id=actor.id,
            path=staged,
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
        db_session.commit()
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                "-u",
                "-c",
                "import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW); "
                "fcntl.flock(fd,fcntl.LOCK_SH); print(bytes([111,119,110,101,100]).decode(),flush=True); sys.stdin.read()",
                str(staged),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            assert child.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=5), "input owner never acquired its FD"
                ready = child.stdout.readline()
                assert ready == b"owned\n", (
                    child.stderr.read() if ready == b"" else ready
                )
            assert staged.read_bytes() == body

            os.killpg(child.pid, signal.SIGKILL)
            assert child.wait(timeout=10) == -signal.SIGKILL
            staging_cleanup.reconcile_jobs(db_session)
            db_session.commit()

            assert not staged.exists()
            assert db_session.get(StagingLease, lease.id) is None
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=10)

    def test_old_actor_preserves_input_across_immediate_retry(
        self,
        staged_input,
        db_session,
        monkeypatch,
    ):
        from app.db.models import Job, User
        from app.modules.work import service

        request, path, lease = staged_input
        actor = db_session.get(User, request.owner_user_id)
        assert actor is not None
        old_epoch = db_session.get(Job, request.job_id).execution_epoch
        custody = staging_leases.open_job_input(db_session, request.job_id)
        monkeypatch.setattr(service, "nudge", lambda *_args: None)

        with custody:
            service.retry(request.job_id, actor=actor)
            db_session.expire_all()
            retried = db_session.get(Job, request.job_id)
            assert retried.state == JobState.QUEUED
            assert retried.execution_epoch != old_epoch
            assert staging_cleanup.release_job(db_session, request.job_id) == 0
            db_session.commit()

            assert path.read_bytes() == b"original retry input"
            assert db_session.get(StagingLease, lease.id) is not None

    def test_expiry_preserves_terminal_input_held_by_actor(
        self, staged_input, db_session
    ):
        request, path, lease = staged_input
        lease.expires_at = utcnow() - timedelta(hours=1)
        db_session.add(lease)
        db_session.commit()
        custody = staging_leases.open_job_input(db_session, request.job_id)

        with custody:
            assert staging_cleanup.prune_expired(db_session) == (0, 0)
            db_session.commit()

            assert path.read_bytes() == b"original retry input"
            assert db_session.get(StagingLease, lease.id) is not None

    def test_discard_preserves_input_held_by_actor(self, staged_input, db_session):
        from app.core.errors import OperationError
        from app.db.models import User

        request, path, lease = staged_input
        actor = db_session.get(User, request.owner_user_id)
        assert actor is not None
        custody = staging_leases.open_job_input(db_session, request.job_id)

        with custody:
            with pytest.raises(OperationError, match="staging_ownership_uncertain"):
                staging_cleanup.discard(request.job_id, actor=actor)

            assert path.read_bytes() == b"original retry input"
            db_session.expire_all()
            assert db_session.get(StagingLease, lease.id) is not None
