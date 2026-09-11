"""Shared thumbnail references remain stable for legacy and versioned assets."""

from app.modules.library.model_views.thumbnails import thumb_url
from tests.factories import detached_model


class TestThumbUrl:
    def test_prefers_thumbnail_file_id(self) -> None:
        model = detached_model(thumbnail_file_id=7, thumbnail_path="99.png")
        assert thumb_url(model) == "/api/v1/files/7/thumbnail"

    def test_versioned_path_changes_the_asset_cache_key(self) -> None:
        model = detached_model(
            thumbnail_file_id=7,
            thumbnail_path="thumbs/7-aaaaaaaaaaaa-recipe.webp",
        )

        url = thumb_url(model)

        assert url is not None
        assert url.startswith("/api/v1/files/7/thumbnail?v=")

    def test_falls_back_to_legacy_digit_stem_path(self) -> None:
        model = detached_model(thumbnail_path="uploads/42.png")
        assert thumb_url(model) == "/api/v1/files/42/thumbnail"

    def test_non_digit_legacy_stem_returns_none(self) -> None:
        model = detached_model(thumbnail_path="uploads/legacy.png")
        assert thumb_url(model) is None

    def test_no_thumbnail_at_all_returns_none(self) -> None:
        model = detached_model()
        assert thumb_url(model) is None
