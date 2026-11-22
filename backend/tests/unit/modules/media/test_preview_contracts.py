"""A published view set is one rendering at one 4:3 Model image quality size."""

import hashlib

import pytest
from pydantic import ValidationError

from app.modules.media.preview_contracts import (
    PreviewManifest,
    PublishedView,
    ViewId,
    ViewProfile,
    ViewRole,
)

_IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _view(index: int, width: int, height: int, offset: int = 0) -> PublishedView:
    return PublishedView(
        id=ViewId(f"orbit-{index}"),
        role=ViewRole.ORBIT,
        rotation=_IDENTITY,
        azimuth_degrees=0.0,
        elevation_degrees=30.0,
        width=width,
        height=height,
        view_center=(0.0, 0.0),
        pixels_per_unit=1.0,
        offset=offset,
        size=10,
        sha256=hashlib.sha256(b"x").hexdigest(),
    )


class TestPublishedView:
    @pytest.mark.parametrize(
        ("width", "height"), [(320, 240), (480, 360), (640, 480), (1280, 960)], ids=str
    )
    def test_accepts_every_supported_size(self, width, height):
        assert _view(0, width, height).height == height

    @pytest.mark.parametrize(
        ("width", "height"), [(640, 360), (319, 239), (1281, 961)], ids=str
    )
    def test_rejects_sizes_outside_the_setting(self, width, height):
        with pytest.raises(ValidationError):
            _view(0, width, height)


class TestPreviewManifest:
    def test_rejects_a_set_rendered_at_two_sizes(self):
        with pytest.raises(ValidationError, match="invalid_preview_dimensions"):
            PreviewManifest(
                recipe=4,
                source_sha256="a" * 64,
                profile=ViewProfile.ORBIT,
                center=(0.0, 0.0, 0.0),
                views=(_view(0, 640, 480), _view(1, 320, 240, offset=10)),
                size=20,
            )
