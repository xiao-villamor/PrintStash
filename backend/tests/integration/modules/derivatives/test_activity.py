"""Preview counts include undiscovered work and never disguise failures as success."""

from datetime import datetime, timezone

import pytest

from app.db.models import SENTINEL_FILE_HASH, DerivativeKind, DerivativeState, FileType
from app.modules.derivatives.activity import preview_activity

BEFORE = datetime(2026, 1, 1, tzinfo=timezone.utc)
AFTER = datetime(2026, 1, 2, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def enabled_preview_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings.frozen, "derivatives_previews_enabled", True)


class TestPreviewActivity:
    def test_counts_work_before_jobs_exist(self, db_session, make_model, make_file):
        make_file(make_model(), filename="part.stl")

        assert preview_activity(db_session).model_dump() == {
            "total": 2,
            "completed": 0,
            "remaining": 2,
            "skipped": 0,
            "failed": 0,
            "latest_failure": None,
        }

    def test_counts_completed_outputs(
        self, db_session, make_model, make_file, make_derivative
    ):
        file = make_file(make_model(), filename="part.stl")
        make_derivative(file, DerivativeKind.THUMBNAIL)
        make_derivative(file, DerivativeKind.PREVIEWS)

        assert preview_activity(db_session).model_dump() == {
            "total": 2,
            "completed": 2,
            "remaining": 0,
            "skipped": 0,
            "failed": 0,
            "latest_failure": None,
        }

    @pytest.mark.parametrize(
        "state",
        [DerivativeState.QUEUED, DerivativeState.RUNNING, DerivativeState.FAILED],
    )
    def test_keeps_unfinished_attempts_remaining(
        self, db_session, make_model, make_file, make_derivative, state
    ):
        file = make_file(make_model(), filename="part.stl")
        make_derivative(file, DerivativeKind.THUMBNAIL, state=state)

        assert preview_activity(db_session).remaining == 2

    def test_reports_exhausted_failures(
        self, db_session, make_model, make_file, make_derivative
    ):
        file = make_file(make_model(), filename="part.stl")
        make_derivative(
            file,
            DerivativeKind.PREVIEWS,
            state=DerivativeState.FAILED,
            exhausted=True,
            failure_reason="resource_limit",
            updated_at=AFTER,
        )

        assert preview_activity(db_session).model_dump() == {
            "total": 2,
            "completed": 0,
            "remaining": 1,
            "skipped": 0,
            "failed": 1,
            "latest_failure": {"reason": "resource_limit", "at": AFTER},
        }

    def test_reports_the_latest_terminal_failure_across_producers(
        self, db_session, make_model, make_file, make_derivative
    ):
        file = make_file(make_model(), filename="part.stl")
        make_derivative(
            file,
            DerivativeKind.PREVIEWS,
            state=DerivativeState.FAILED,
            exhausted=True,
            failure_reason="resource_limit",
            updated_at=BEFORE,
        )
        make_derivative(
            file,
            DerivativeKind.THUMBNAIL,
            state=DerivativeState.FAILED,
            exhausted=True,
            failure_reason="storage",
            updated_at=AFTER,
        )
        other = make_file(make_model(), filename="other.stl")
        make_derivative(other, DerivativeKind.PREVIEWS, state=DerivativeState.FAILED)

        failure = preview_activity(db_session).latest_failure
        assert failure is not None
        assert failure.model_dump() == {"reason": "storage", "at": AFTER}

    @pytest.mark.parametrize(
        "state", [DerivativeState.SKIPPED, DerivativeState.CANCELLED]
    )
    def test_reports_outputs_that_will_not_be_generated(
        self, db_session, make_model, make_file, make_derivative, state
    ):
        file = make_file(make_model(), filename="part.stl")
        make_derivative(file, DerivativeKind.PREVIEWS, state=state)

        assert preview_activity(db_session).skipped == 1

    def test_excludes_disabled_multiangle_generation(
        self, db_session, make_model, make_file, make_system_config
    ):
        make_file(make_model(), filename="part.stl")
        make_system_config(derivatives_previews_enabled=False)

        assert preview_activity(db_session).total == 1

    @pytest.mark.parametrize(
        "file_type",
        [
            FileType.STL,
            FileType.THREE_MF,
            FileType.STEP,
            FileType.GCODE,
            FileType.RESIN,
        ],
    )
    def test_reads_the_formats_current_cover_recipe(
        self, db_session, make_model, make_file, make_derivative, file_type
    ):
        file = make_file(make_model(), file_type=file_type)
        make_derivative(file, DerivativeKind.THUMBNAIL)
        make_derivative(file, DerivativeKind.THUMBNAIL, recipe_version=0)

        assert preview_activity(db_session).completed == 1

    def test_old_multiangle_recipe_is_remaining(
        self, db_session, make_model, make_file, make_derivative
    ):
        file = make_file(make_model(), filename="part.stl")
        make_derivative(file, DerivativeKind.PREVIEWS, recipe_version=1)

        assert preview_activity(db_session).remaining == 2

    def test_regeneration_reopens_completed_images(
        self,
        db_session,
        make_model,
        make_file,
        make_derivative,
        make_derivative_regeneration,
    ):
        file = make_file(make_model(), filename="part.stl")
        make_derivative(file, DerivativeKind.THUMBNAIL, updated_at=BEFORE)
        make_derivative_regeneration(DerivativeKind.THUMBNAIL, requested_at=AFTER)

        assert preview_activity(db_session).remaining == 2

    def test_excludes_ineligible_files(self, db_session, make_model, make_file):
        make_file(make_model(), filename="part.stl", trashed=True)
        make_file(make_model(), filename="part.stl", sha256=SENTINEL_FILE_HASH)

        assert preview_activity(db_session).total == 0

    def test_empty_library_is_complete(self, db_session):
        assert preview_activity(db_session).model_dump() == {
            "total": 0,
            "completed": 0,
            "remaining": 0,
            "skipped": 0,
            "failed": 0,
            "latest_failure": None,
        }
