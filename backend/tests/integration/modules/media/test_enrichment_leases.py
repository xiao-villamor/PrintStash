"""Long calculations retain one shared permit; stale claims cannot revive it."""

from datetime import timedelta
from threading import Event

from app.core.time import utcnow
from app.db.models import (
    ArtifactAnalysisGeneration,
    ThumbnailGeneration,
    ThumbnailRenderSlot,
)
from app.db.session import get_session_factory
from app.modules.media import analysis_generations, compute_slots, enrichment_leases
from app.modules.media.thumbnail_generations import claim_thumbnail
from tests.factories import build_file, build_model


class _FirstTickEvent:
    """Advance one heartbeat interval, then wait for ordinary context shutdown."""

    def __init__(self):
        self.stopped = Event()
        self.settled = Event()
        self.first = True

    def wait(self, _timeout):
        if self.first:
            self.first = False
            return False
        self.settled.set()
        return self.stopped.wait(timeout=10)

    def set(self):
        self.stopped.set()


class TestEnrichmentLeasesContract:
    def test_background_heartbeat_retains_both_outputs_with_the_shared_permit(
        self, threaded_hub_db, monkeypatch
    ):
        sessions = get_session_factory()
        with sessions.scoped_session() as session:
            file = build_file(session, build_model(session))
            analysis_generations.request_enrichment(
                session, file, promote_thumbnail=True
            )
            session.commit()
            metadata = analysis_generations.claim_next(session)
            preview = claim_thumbnail(session, file)
            slot = compute_slots.acquire(session, metadata.token, lease_seconds=60)
            slot_id, before = slot.id, slot.lease_expires_at
        clock = _FirstTickEvent()
        monkeypatch.setattr(enrichment_leases, "Event", lambda: clock)

        with enrichment_leases.keep_alive(
            sessions, slot_id, metadata.token, metadata, preview
        ):
            assert clock.settled.wait(timeout=5)
            with sessions.scoped_session() as session:
                permit_expiry = session.get(
                    ThumbnailRenderSlot, slot_id
                ).lease_expires_at
                assert permit_expiry > before
                assert (
                    session.get(
                        ArtifactAnalysisGeneration, metadata.generation_id
                    ).lease_expires_at
                    == permit_expiry
                )
                assert (
                    session.get(
                        ThumbnailGeneration, preview.generation_id
                    ).lease_expires_at
                    == permit_expiry
                )

        assert clock.stopped.is_set()

    def test_renews_the_output_claim_with_its_compute_permit(
        self, db_session, make_model, make_file, make_artifact_analysis
    ):
        file = make_file(make_model())
        generation = make_artifact_analysis(file)
        claim = analysis_generations.claim_next(db_session)
        slot = compute_slots.acquire(db_session, claim.token, lease_seconds=60)
        before = slot.lease_expires_at
        assert enrichment_leases.renew(db_session, slot.id, claim.token, claim, None)
        db_session.refresh(slot)
        db_session.refresh(generation)
        assert slot.lease_expires_at > before
        assert slot.lease_expires_at == generation.lease_expires_at

    def test_lost_output_claim_does_not_extend_the_permit(
        self, db_session, make_model, make_file, make_artifact_analysis
    ):
        file = make_file(make_model())
        generation = make_artifact_analysis(file)
        claim = analysis_generations.claim_next(db_session)
        slot = compute_slots.acquire(db_session, claim.token, lease_seconds=60)
        slot_id, before = slot.id, slot.lease_expires_at
        generation.lease_token = "successor"
        db_session.add(generation)
        db_session.commit()
        assert (
            enrichment_leases.renew(db_session, slot_id, claim.token, claim, None)
            is False
        )
        db_session.expire_all()
        assert db_session.get(ThumbnailRenderSlot, slot_id).lease_expires_at == before

    def test_expired_permit_cannot_be_revived(self, db_session):
        slot = compute_slots.acquire(db_session, "expired")
        slot.lease_expires_at = utcnow() - timedelta(seconds=1)
        db_session.add(slot)
        db_session.commit()
        assert (
            enrichment_leases.renew(db_session, slot.id, "expired", None, None) is False
        )
