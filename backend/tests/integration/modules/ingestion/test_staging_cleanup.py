"""Expiry cannot delete a source while durable work still depends on it."""

import hashlib
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.time import utcnow
from app.db.models import StagingLease
from app.modules.ingestion import commands, staging_cleanup, staging_leases
from app.runtime.jobs import registry


class TestPruneExpired:
    @pytest.mark.parametrize(
        "dependent", [False, True], ids=["source", "dependent-child"]
    )
    def test_retains_bytes_until_durable_owners_finish(
        self, db_session, tmp_path, dependent
    ):
        source = registry.create(session=db_session)
        commands.enqueue(db_session, source, "artifact", {})
        child = None
        if dependent:
            child = registry.create(
                session=db_session,
                kind="capture_enrichment",
                job_id=commands.capture_enrichment_job_id(source),
            )
            commands.enqueue(
                db_session, child, "capture_enrichment", {"source_job_id": source}
            )
        path = tmp_path / "source.stl"
        path.write_bytes(b"authoritative source")
        staging_leases.record_job_lease(
            db_session,
            job_id=source,
            staged=path,
            size=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            owner_user_id=None,
        )
        lease = db_session.exec(select(StagingLease)).one()
        lease.expires_at = utcnow() - timedelta(days=1)
        db_session.add(lease)
        db_session.commit()
        if dependent:
            registry.finish(source, state="completed")

        assert staging_cleanup.prune_expired(db_session) == (0, 0)
        db_session.commit()
        assert path.read_bytes() == b"authoritative source"
        assert db_session.exec(select(StagingLease)).one().id == lease.id

        registry.finish(child or source, state="completed")
        assert staging_cleanup.prune_expired(db_session) == (1, 1)
        db_session.commit()
        assert not path.exists()
        assert db_session.exec(select(StagingLease)).all() == []
