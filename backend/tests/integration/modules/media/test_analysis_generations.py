"""Metadata work obeys source identity, supplied facts and retry admission."""

from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import ArtifactAnalysisGeneration, Metadata, ThumbnailGeneration
from app.modules.media import analysis_generations as analysis


@pytest.fixture
def requested(db_session, make_model, make_file):
    file = make_file(make_model(), metadata={"triangle_count": 7, "bbox_x_mm": 12.5})
    analysis.request_enrichment(db_session, file, promote_thumbnail=True)
    db_session.commit()
    return file


class TestAnalysisGenerationsContract:
    def test_registration_is_rolled_back_with_its_transaction(
        self, db_session, make_model, make_file
    ):
        file = make_file(make_model())
        analysis.request_enrichment(db_session, file, promote_thumbnail=True)
        db_session.rollback()
        assert db_session.exec(select(ArtifactAnalysisGeneration)).all() == []
        assert db_session.exec(select(ThumbnailGeneration)).all() == []

    def test_preserves_metadata_supplied_by_a_library_transfer(
        self, db_session, make_model, make_file
    ):
        file = make_file(
            make_model(), metadata={"triangle_count": 7, "bbox_x_mm": 12.5}
        )
        analysis.request_enrichment(
            db_session, file, promote_thumbnail=False, preserve_metadata=True
        )
        db_session.commit()
        claim = analysis.claim_next(db_session)
        assert analysis.publish_metadata(
            db_session, claim, {"triangle_count": 99, "bbox_x_mm": 100}
        )
        values = db_session.exec(
            select(Metadata).where(Metadata.file_id == file.id)
        ).one()
        assert (values.triangle_count, values.bbox_x_mm) == (7, 12.5)

    def test_expired_metadata_work_is_recoverable(self, db_session, requested):
        previous = analysis.claim_next(db_session)
        row = db_session.get(ArtifactAnalysisGeneration, previous.generation_id)
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db_session.add(row)
        db_session.commit()
        current = analysis.claim_next(db_session)
        assert current.file_id == requested.id
        assert current.token != previous.token

    @pytest.mark.parametrize("change", ["trashed", "changed"])
    def test_rejects_metadata_for_a_source_that_is_no_longer_current(
        self, db_session, requested, change
    ):
        claim = analysis.claim_next(db_session)
        if change == "trashed":
            requested.deleted_at = utcnow()
        else:
            requested.sha256 = "b" * 64
        db_session.add(requested)
        db_session.commit()
        assert (
            analysis.publish_metadata(db_session, claim, {"triangle_count": 99})
            is False
        )
        values = db_session.exec(
            select(Metadata).where(Metadata.file_id == requested.id)
        ).one()
        assert values.triangle_count == 7

    def test_capacity_wait_does_not_consume_analysis_attempts(
        self, db_session, requested
    ):
        claim = analysis.claim_next(db_session)
        analysis.defer_analysis(db_session, claim, "capacity_busy")
        db_session.expire_all()
        row = db_session.get(ArtifactAnalysisGeneration, claim.generation_id)
        assert row.attempts == 0
        assert row.state == "pending"
        assert analysis.claim_next(db_session) is None

    def test_disabled_previews_do_not_block_metadata_registration(
        self, db_session, make_model, make_file
    ):
        _overlay["thumbnail_processing"] = "disabled"
        file = make_file(make_model())
        analysis.request_enrichment(db_session, file, promote_thumbnail=True)
        db_session.commit()
        assert (
            db_session.exec(select(ThumbnailGeneration.processing_policy)).one()
            == "disabled"
        )
        assert analysis.claim_next(db_session).file_id == file.id

    def test_marks_metadata_ready_only_for_the_current_claim(
        self, db_session, requested
    ):
        claim = analysis.claim_next(db_session)
        assert analysis.publish_metadata(db_session, claim, {"triangle_count": 99})
        assert analysis.finish_analysis(db_session, claim)
        db_session.expire_all()
        assert (
            db_session.get(ArtifactAnalysisGeneration, claim.generation_id).state
            == "ready"
        )
        assert analysis.claim_next(db_session) is None

    def test_leaves_obsolete_analysis_recipes_unclaimed(self, db_session, requested):
        generation = db_session.exec(select(ArtifactAnalysisGeneration)).one()
        generation.recipe = "metadata-v0"
        db_session.add(generation)
        db_session.commit()

        assert analysis.claim_next(db_session) is None

        db_session.refresh(generation)
        assert generation.state == "pending"
        assert generation.lease_token is None

    def test_rejects_publication_after_recipe_changes(self, db_session, requested):
        claim = analysis.claim_next(db_session)
        generation = db_session.get(ArtifactAnalysisGeneration, claim.generation_id)
        generation.recipe = "metadata-v0"
        db_session.add(generation)
        db_session.commit()

        assert (
            analysis.publish_metadata(db_session, claim, {"triangle_count": 99})
            is False
        )
        assert analysis.finish_analysis(db_session, claim) is False

        db_session.expire_all()
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == requested.id)
        ).one()
        assert metadata.triangle_count == 7
        assert (
            db_session.get(ArtifactAnalysisGeneration, claim.generation_id).state
            != "ready"
        )
