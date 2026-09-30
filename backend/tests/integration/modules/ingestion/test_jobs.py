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
        self, name: str, lane: str
    ) -> None:
        # Network-bound imports must not queue behind local hashing.
        assert DEFINITIONS[name].lane == lane

    def test_every_import_is_requested_not_discovered(self) -> None:
        assert all(definition.source is None for definition in DEFINITIONS.values())


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
