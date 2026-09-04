"""Multipart cover URLs are validated before a set draft reaches persistence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.multipart_models import MultipartModelSave


class TestMultipartModelSave:
    def test_accepts_an_http_image_url_for_the_set_cover(self) -> None:
        payload = MultipartModelSave.model_validate(
            {
                "cover_image_url": "http://images.example.test/covers/dragon.webp",
                "parts": [],
            }
        )

        assert str(payload.cover_image_url) == (
            "http://images.example.test/covers/dragon.webp"
        )

    def test_rejects_a_non_http_set_cover_url(self) -> None:
        with pytest.raises(ValidationError):
            MultipartModelSave.model_validate(
                {"cover_image_url": "javascript:alert(1)", "parts": []}
            )
