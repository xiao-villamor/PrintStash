"""Disabled image generation must not keep readers polling for pending views."""

from app.modules.library.model_views.previews import summaries


class TestSummaries:
    def test_reports_disabled_generation(
        self, db_session, make_model, make_file, make_system_config
    ):
        model = make_model()
        file = make_file(model, filename="part.stl")
        make_system_config(derivatives_previews_enabled=False)

        result = summaries(db_session, [model])[model.id]

        assert result.model_dump() == {
            "kind": "artifact",
            "file_id": file.id,
            "cover_camera": "unknown",
            "state": "disabled",
            "reason": None,
            "published": None,
        }

    def test_fresh_install_has_only_the_ordinary_thumbnail(
        self, db_session, make_model, make_file
    ):
        from app.modules.derivatives.activity import preview_activity

        model = make_model()
        make_file(model, filename="part.stl")
        result = summaries(db_session, [model])[model.id]
        assert result.state == "disabled"
        assert result.published is None
        assert preview_activity(db_session).total == 1
