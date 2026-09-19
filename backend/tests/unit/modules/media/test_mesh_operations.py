"""Compatibility callers retain thumbnail completeness and fingerprint data."""

from pathlib import Path

import pytest

from app.modules.media import mesh_operations, thumbnail_engine
from app.modules.media.fingerprints import FingerprintResult, GeometryMetadata
from app.modules.media.mesh_processing import FallbackThumbnail
from app.modules.media.thumbnail_engine import ThumbnailResult, ThumbnailStrategy


class TestMeshOperations:
    @pytest.mark.parametrize("operation", ["analyze_mesh", "render_thumbnail"])
    @pytest.mark.parametrize(
        "strategy",
        [
            ThumbnailStrategy.FULL,
            ThumbnailStrategy.STREAMING,
            ThumbnailStrategy.FALLBACK,
        ],
    )
    @pytest.mark.parametrize("complete", [False, True])
    def test_preserves_fallback_completeness(
        self, monkeypatch, operation, strategy, complete
    ):
        result = ThumbnailResult(
            b"image", {"triangle_count": 12}, strategy, complete, None, 1, None
        )
        monkeypatch.setattr(
            thumbnail_engine.ThumbnailEngine, "generate", lambda _self, _request: result
        )
        output = getattr(mesh_operations, operation)(Path("part.stl"))
        if operation == "analyze_mesh":
            geometry, output = output
            assert geometry == {"triangle_count": 12}
        assert output == b"image"
        if strategy != ThumbnailStrategy.FULL:
            assert isinstance(output, FallbackThumbnail)
            assert output.complete == complete
        else:
            assert type(output) is bytes

    def test_returns_a_separate_fingerprint(self, monkeypatch):
        fingerprint = FingerprintResult("failed", failure_code="resource_limit")
        result = ThumbnailResult(
            None,
            {"triangle_count": 12},
            ThumbnailStrategy.NONE,
            False,
            None,
            1,
            None,
            fingerprint,
        )
        monkeypatch.setattr(
            thumbnail_engine.ThumbnailEngine, "generate", lambda _self, _request: result
        )

        geometry, image = mesh_operations.analyze_mesh(
            Path("part.stl"), include_fingerprint=True
        )

        assert isinstance(geometry, GeometryMetadata)
        assert geometry == {"triangle_count": 12}
        assert geometry.fingerprint_result == fingerprint
        assert image is None
