"""Durable intake survives failed, delayed and superseded execution."""

import asyncio
from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.db.models import BackgroundJob
from app.modules.ingestion import commands
from app.runtime import ingestion, maintenance
from app.runtime.jobs import registry


class TestProcessOne:
    @pytest.mark.asyncio
    async def test_defers_a_dependency_without_consuming_retries(self, db_session):
        source = registry.create(session=db_session)
        child = registry.create(session=db_session, kind="capture_enrichment")
        commands.enqueue(
            db_session, child, "capture_enrichment", {"source_job_id": source}
        )
        db_session.commit()

        assert await ingestion.process_one(enrichment=True)

        db_session.expire_all()
        row = db_session.get(BackgroundJob, child)
        assert (row.state, row.attempts, row.claim_token) == ("pending", 0, None)
        assert row.next_attempt_at.replace(tzinfo=None) > utcnow().replace(tzinfo=None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("attempts", [0, 7], ids=["retry", "exhausted"])
    async def test_bounds_optional_command_retries(
        self, db_session, monkeypatch, attempts
    ):
        job = registry.create(session=db_session)
        commands.enqueue(db_session, job, "artifact", {})
        row = db_session.get(BackgroundJob, job)
        row.attempts = attempts
        db_session.add(row)
        db_session.commit()

        async def deferred(_claim, _sessions):
            raise commands.CommandDeferred("optional_stage_unavailable")

        monkeypatch.setattr(ingestion.command_executor, "execute", deferred)
        assert await ingestion.process_one()

        db_session.expire_all()
        row = db_session.get(BackgroundJob, job)
        assert row.claim_token is None
        assert row.attempts == attempts + 1
        status = registry.get(job)
        if attempts == 7:
            assert status.state == "failed"
            assert status.error == "capture_enrichment_failed"
            assert status.retryable is True
        else:
            assert status.state == "pending"
            assert row.next_attempt_at.replace(tzinfo=None) > utcnow().replace(
                tzinfo=None
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("safe", [False, True], ids=["unexpected", "import-error"])
    async def test_sanitizes_command_failures(self, db_session, monkeypatch, safe):
        from app.modules.ingestion.importer import ImportError_

        job = registry.create(session=db_session)
        commands.enqueue(db_session, job, "artifact", {})
        db_session.commit()

        async def failed(_claim, _sessions):
            if safe:
                raise ImportError_("unsupported_archive")
            raise ValueError("private-source-path")

        monkeypatch.setattr(ingestion.command_executor, "execute", failed)
        assert await ingestion.process_one()

        status = registry.get(job)
        assert status.state == "failed"
        assert status.error == (
            "unsupported_archive" if safe else "ingestion_command_failed"
        )
        assert status.retryable is True
        db_session.expire_all()
        assert db_session.get(BackgroundJob, job).claim_token is None

    @pytest.mark.asyncio
    async def test_preserves_successor_ownership_after_failure(
        self, db_session, monkeypatch
    ):
        job = registry.create(session=db_session)
        commands.enqueue(db_session, job, "artifact", {})
        db_session.commit()

        async def superseded(claim, sessions):
            with sessions.scoped_session() as session:
                row = session.get(BackgroundJob, claim.job_id)
                row.claim_token = "successor"
                row.lease_expires_at = utcnow() + timedelta(minutes=5)
                session.add(row)
                session.commit()
            raise ValueError("old-worker-failure")

        monkeypatch.setattr(ingestion.command_executor, "execute", superseded)
        assert await ingestion.process_one()

        db_session.expire_all()
        row = db_session.get(BackgroundJob, job)
        assert (row.state, row.claim_token) == ("pending", "successor")
        assert registry.get(job).error is None

    @pytest.mark.asyncio
    async def test_renews_a_running_command_lease(
        self, threaded_hub_db, db_session, monkeypatch
    ):
        job = registry.create(session=db_session)
        commands.enqueue(db_session, job, "artifact", {})
        db_session.commit()
        # Exercise real SQL renewal with the production lease duration.
        # Shortening expiry makes this test lose its claim under build load.
        sleep = asyncio.sleep
        renewed_event = asyncio.Event()
        block_second_heartbeat = asyncio.Event()
        heartbeat_waits = 0

        async def heartbeat_sleep(delay):
            nonlocal heartbeat_waits
            if delay != commands.LEASE_SECONDS / 3:
                await sleep(delay)
                return
            heartbeat_waits += 1
            if heartbeat_waits == 1:
                await sleep(0.01)
                return
            await block_second_heartbeat.wait()

        monkeypatch.setattr(ingestion.asyncio, "sleep", heartbeat_sleep)
        original_renew = commands.renew
        renewed = []

        def record_renewal(session, claim):
            before = session.get(BackgroundJob, job).lease_expires_at
            changed = original_renew(session, claim)
            session.expire_all()
            after = session.get(BackgroundJob, job).lease_expires_at
            if changed:
                renewed.append((before, after))
                renewed_event.set()
            return changed

        monkeypatch.setattr(commands, "renew", record_renewal)

        async def held(claim, sessions):
            await asyncio.wait_for(renewed_event.wait(), timeout=3)
            registry.finish(claim.job_id, state="completed")

        monkeypatch.setattr(ingestion.command_executor, "execute", held)
        assert await ingestion.process_one()
        assert renewed
        assert all(
            before is not None and after is not None and after > before
            for before, after in renewed
        )
        assert registry.get(job).state == "completed"

    @pytest.mark.asyncio
    async def test_defers_admission_during_restore(self):
        maintenance.begin_restore_maintenance()
        try:
            assert await ingestion.process_one() is False
        finally:
            maintenance.end_restore_maintenance()
