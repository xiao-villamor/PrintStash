"""Staging summaries stay private and distinguish ingest from other jobs."""

import hashlib

from app.db.models import JobState
from app.modules.ingestion.staging_leases import create_job_lease
from app.modules.work.jobs import jobs


class TestStagingViews:
    def test_sums_multiple_inputs_without_exposing_paths(
        self, db_session, make_ingest_request, make_user, tmp_path
    ):
        request = make_ingest_request(make_user(), state=JobState.FAILED)
        for index, data in enumerate((b"first", b"second")):
            path = tmp_path / str(index)
            path.write_bytes(data)
            create_job_lease(
                db_session,
                job_id=request.job_id,
                owner_user_id=request.owner_user_id,
                path=path,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        db_session.commit()
        status = jobs.get(request.job_id)
        assert status is not None and status.staging is not None
        assert status.staging.retained_bytes == 11
        assert status.staging.lease_count == 2
        assert status.staging.discard_available
        assert str(tmp_path) not in status.model_dump_json()

    def test_non_ingest_staging_cannot_offer_discard(
        self, db_session, make_job, make_user, tmp_path
    ):
        owner = make_user()
        job = make_job(owner=owner, state=JobState.FAILED)
        data = b"other work input"
        path = tmp_path / "other"
        path.write_bytes(data)
        create_job_lease(
            db_session,
            job_id=job.id,
            owner_user_id=owner.id,
            path=path,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        db_session.commit()
        status = jobs.get(job.id)
        assert status is not None and status.staging is not None
        assert not status.staging.discard_available
