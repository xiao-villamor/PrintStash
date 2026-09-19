"""Source admission has bounded priority over optional derivative queues."""

from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.modules.ingestion.commands import enqueue
from app.runtime.jobs import registry
from app.runtime.work_priority import defer_background


@pytest.fixture
def source_work(db_session):
    job_id = registry.create(session=db_session)
    enqueue(db_session, job_id, "artifact", {})
    db_session.commit()


class TestWorkPriorityContract:
    def test_new_derivatives_yield_to_source_work(self, db_session, source_work):
        assert defer_background(db_session, "enrichment") is True


    def test_aged_analysis_gets_a_turn_during_continuous_intake(self,
        db_session, source_work, make_model, make_file, make_artifact_analysis
    ):
        file = make_file(make_model())
        make_artifact_analysis(file, created_at=utcnow() - timedelta(seconds=61))
        assert defer_background(db_session, "enrichment") is False


    def test_aged_preview_gets_a_turn_after_analysis_is_ready(self,
        db_session, source_work, make_model, make_file, make_thumbnail_generation
    ):
        file = make_file(make_model())
        make_thumbnail_generation(file, created_at=utcnow() - timedelta(seconds=61))
        assert defer_background(db_session, "enrichment") is False


    def test_on_demand_previews_do_not_compete_until_requested(self,
        db_session, source_work, make_model, make_file, make_thumbnail_generation
    ):
        file = make_file(make_model())
        make_thumbnail_generation(
            file, processing_policy="on_demand", created_at=utcnow() - timedelta(seconds=61)
        )
        assert defer_background(db_session, "enrichment") is True


    def test_background_work_runs_when_intake_is_empty(self, db_session):
        assert defer_background(db_session, "enrichment") is False


    def test_aged_similarity_gets_a_turn_during_continuous_intake(self, db_session, source_work, make_similarity_run, make_user):
        make_similarity_run(make_user(), created_at=utcnow() - timedelta(seconds=61))
        assert defer_background(db_session, "similarity") is False
