"""Staging recovery releases only proven bytes after work has settled."""

import hashlib
from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.db.models import IngestRequestKind, JobState, StagingLease
from app.modules.ingestion import staging_cleanup, staging_leases
from app.modules.ingestion.ingestion import release_job_staging


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
        release_job_staging(request.job_id)
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
