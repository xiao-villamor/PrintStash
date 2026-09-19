"""Saved sources expose independent readiness without inventing completed facts."""

import pytest

from app.modules.ingestion.commands import capture_enrichment_job_id
from app.modules.library.model_views.enrichment import pending_models, states_for_files


class TestStatesForFiles:
    @pytest.mark.parametrize(
        "analysis_state,analysis_error,preview_state,policy,preview_error,expected",
        [
            ("ready", None, "pending", "background", None, ("ready", "pending")),
            (
                "pending",
                "source_unavailable",
                "ready",
                "background",
                None,
                ("blocked", "ready"),
            ),
            (
                "ready",
                None,
                "pending",
                "background",
                "capacity_busy",
                ("ready", "blocked"),
            ),
            (
                "ready",
                None,
                "failed",
                "background",
                "no_embedded_thumbnail",
                ("ready", "not_applicable"),
            ),
            ("ready", None, "pending", "on_demand", None, ("ready", "on_demand")),
            ("ready", None, "pending", "disabled", None, ("ready", "disabled")),
            (
                "failed",
                "invalid_geometry",
                "ready",
                "disabled",
                None,
                ("failed", "ready"),
            ),
        ],
        ids=[
            "preview-pending",
            "source-blocked",
            "preview-blocked",
            "no-preview",
            "on-demand",
            "disabled",
            "existing-ready",
        ],
    )
    def test_reports_independent_readiness(
        self,
        db_session,
        make_model,
        make_file,
        make_artifact_analysis,
        make_thumbnail_generation,
        analysis_state,
        analysis_error,
        preview_state,
        policy,
        preview_error,
        expected,
    ):
        file = make_file(make_model())
        make_artifact_analysis(file, state=analysis_state, error_code=analysis_error)
        make_thumbnail_generation(
            file,
            state=preview_state,
            processing_policy=policy,
            failure_reason=preview_error,
        )

        result = states_for_files(db_session, [file.id])[file.id]

        assert (result.metadata, result.thumbnail) == expected
        assert result.metadata_error == analysis_error
        assert result.thumbnail_error == preview_error

    def test_reports_unmeasured_facts_as_unknown(
        self, db_session, make_model, make_file
    ):
        file = make_file(make_model())

        result = states_for_files(db_session, [file.id])[file.id]

        assert result.metadata == "unknown"
        assert result.thumbnail == "unknown"
        assert result.metadata_error is None
        assert result.thumbnail_error is None

    def test_ignores_generations_for_an_old_source(
        self,
        db_session,
        make_model,
        make_file,
        make_artifact_analysis,
        make_thumbnail_generation,
    ):
        file = make_file(make_model())
        make_artifact_analysis(file, source_sha256="b" * 64)
        make_thumbnail_generation(file, source_sha256="b" * 64)

        result = states_for_files(db_session, [file.id])[file.id]

        assert result.metadata == "unknown"
        assert result.thumbnail == "unknown"
        assert pending_models(db_session, [file.model_id]) == set()

    def test_excludes_trashed_sources(
        self,
        db_session,
        make_model,
        make_file,
        make_artifact_analysis,
        make_thumbnail_generation,
    ):
        file = make_file(make_model(), trashed=True)
        make_artifact_analysis(file)
        make_thumbnail_generation(file)

        assert states_for_files(db_session, [file.id]) == {}
        assert pending_models(db_session, [file.model_id]) == set()

    def test_handles_empty_requests(self, db_session):
        assert states_for_files(db_session, []) == {}
        assert pending_models(db_session, []) == set()


class TestPendingModels:
    @pytest.mark.parametrize(
        "policy,scheduled",
        [("background", True), ("on_demand", False), ("disabled", False)],
    )
    def test_flags_only_scheduled_previews(
        self,
        db_session,
        make_model,
        make_file,
        make_artifact_analysis,
        make_thumbnail_generation,
        policy,
        scheduled,
    ):
        file = make_file(make_model())
        make_artifact_analysis(file, state="ready")
        make_thumbnail_generation(file, processing_policy=policy)

        assert (
            file.model_id in pending_models(db_session, [file.model_id])
        ) is scheduled

    @pytest.mark.parametrize(
        "state,scheduled",
        [("pending", True), ("running", True), ("completed", False), ("failed", False)],
    )
    def test_keeps_capture_work_visible_after_source_completion(
        self,
        db_session,
        make_model,
        make_background_job,
        make_inbox_item,
        make_user,
        state,
        scheduled,
    ):
        model = make_model()
        source = make_background_job(state="completed")
        make_inbox_item(
            make_user(),
            state="completed",
            resulting_model_id=model.id,
            background_job_id=source.id,
        )
        make_background_job(id=capture_enrichment_job_id(source.id), state=state)

        assert (model.id in pending_models(db_session, [model.id])) is scheduled
