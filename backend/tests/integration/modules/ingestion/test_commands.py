"""Accepted commands survive request/process lifetime and have one active owner."""

from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.db.models import BackgroundJob
from app.db.session import get_session_factory
from app.modules.ingestion.commands import claim_next, enqueue, release, renew
from app.runtime.jobs import registry


@pytest.fixture
def queued_job(db_session):
    job_id = registry.create(kind="model", session=db_session)
    enqueue(db_session, job_id, "artifact", {"filename": "cube.stl"})
    db_session.commit()
    return job_id


class TestCommandsContract:
    def test_claims_committed_work_from_a_fresh_session(self, queued_job):
        with get_session_factory().scoped_session() as session:
            claim = claim_next(session)
        assert claim.job_id == queued_job
        assert claim.arguments == {"filename": "cube.stl"}

    def test_does_not_accept_work_on_rollback(self, db_session):
        job_id = registry.create(kind="model", session=db_session)
        enqueue(db_session, job_id, "artifact", {})
        db_session.rollback()
        assert claim_next(db_session) is None

    def test_does_not_claim_work_owned_by_another_worker(self, db_session, queued_job):
        claim_next(db_session)
        assert claim_next(db_session) is None

    def test_recovers_an_expired_claim(self, db_session, queued_job):
        previous = claim_next(db_session)
        row = db_session.get(BackgroundJob, queued_job)
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db_session.add(row)
        db_session.commit()
        recovered = claim_next(db_session)
        assert recovered.job_id == queued_job
        assert recovered.token != previous.token

    def test_stale_owner_cannot_renew_a_reclaimed_job(self, db_session, queued_job):
        previous = claim_next(db_session)
        release(db_session, previous)
        current = claim_next(db_session)
        assert renew(db_session, previous) is False
        assert renew(db_session, current) is True

    def test_rejects_unknown_commands(self, db_session):
        job_id = registry.create(session=db_session)
        with pytest.raises(ValueError, match="unsupported_ingestion_command"):
            enqueue(db_session, job_id, "run_anything", {})

    def test_restart_reconciliation_preserves_an_accepted_command(
        self, db_session, queued_job
    ):
        from app.runtime.jobs import reconcile_interrupted_jobs

        reconcile_interrupted_jobs()
        db_session.expire_all()
        assert db_session.get(BackgroundJob, queued_job).state == "pending"

    def test_reclaimed_worker_cannot_terminalize_the_current_job(
        self, db_session, queued_job
    ):
        from app.modules.ingestion.commands import execution_scope

        previous = claim_next(db_session)
        release(db_session, previous)
        claim_next(db_session)

        with (
            execution_scope(previous),
            pytest.raises(RuntimeError, match="ingestion_claim_lost"),
        ):
            registry.finish(queued_job, state="failed", error="late_failure")

        assert registry.get(queued_job).state == "pending"

    def test_successor_completes_after_a_rejected_stale_callback(
        self, db_session, queued_job
    ):
        from app.modules.ingestion.commands import execution_scope

        previous = claim_next(db_session)
        release(db_session, previous)
        current = claim_next(db_session)

        with (
            execution_scope(previous),
            pytest.raises(RuntimeError, match="ingestion_claim_lost"),
        ):
            registry.finish(queued_job, state="failed", error="stale callback")
        # Do not call registry.get() here: its refresh used to mask the poisoned
        # cache before the valid successor tried to publish its terminal state.
        with execution_scope(current):
            registry.finish(queued_job, state="completed")

        db_session.expire_all()
        assert db_session.get(BackgroundJob, queued_job).state == "completed"

    def test_accepted_cookie_is_encrypted_at_rest(self, db_session):
        from app.core.secrets import decrypt_secret
        from app.modules.ingestion.commands import decode

        job_id = registry.create(session=db_session)
        enqueue(
            db_session,
            job_id,
            "url",
            {
                "request": {
                    "url": "https://example.com/model",
                    "thingiverse_cookie": "test-cookie-value",
                },
                "actor_user_id": 1,
            },
        )
        db_session.commit()
        payload = db_session.get(BackgroundJob, job_id).payload_json
        assert "test-cookie-value" not in payload
        _, arguments = decode(payload)
        assert (
            decrypt_secret(arguments["request"]["thingiverse_cookie_encrypted"])
            == "test-cookie-value"
        )

    def test_dependency_wait_does_not_exhaust_execution_attempts(
        self, db_session, queued_job
    ):
        from app.modules.ingestion.commands import defer

        claim = claim_next(db_session)
        assert defer(db_session, claim, waiting=True)
        db_session.expire_all()
        assert db_session.get(BackgroundJob, queued_job).attempts == 0

    def test_stable_child_creation_preserves_the_callers_transaction(
        self, db_session, make_model
    ):
        model = make_model()
        model.name = "Committed with dependent work"
        db_session.add(model)
        db_session.flush()
        registry.create(
            kind="capture_enrichment", session=db_session, job_id="stable-child"
        )
        db_session.commit()
        db_session.refresh(model)
        assert model.name == "Committed with dependent work"

    def test_pending_source_work_has_priority_over_new_derivatives(
        self, db_session, queued_job
    ):
        from app.modules.ingestion.commands import foreground_pending

        assert foreground_pending(db_session) is True

    def test_optional_capture_work_does_not_block_source_admission(self, db_session):
        from app.modules.ingestion.commands import foreground_pending

        job_id = registry.create(kind="capture_enrichment", session=db_session)
        enqueue(
            db_session,
            job_id,
            "capture_enrichment",
            {"item_id": 1, "source_job_id": "source"},
        )
        db_session.commit()
        assert foreground_pending(db_session) is False
