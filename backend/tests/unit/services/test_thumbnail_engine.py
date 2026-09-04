"""Defend bounded strategy selection in the thumbnail orchestration seam.

These tests keep dense or invalid meshes from bypassing resource limits while
preserving the isolated renderer that fixed issue #67.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import mesh_processing
from app.services.thumbnail_engine import (
    ThumbnailEngine,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailStrategy,
)


def _geometry() -> dict[str, float | int | None]:
    return {
        "bbox_x_mm": 10.0,
        "bbox_y_mm": 20.0,
        "bbox_z_mm": 30.0,
        "volume_mm3": None,
        "triangle_count": 12,
    }


class _Mesh:
    faces = list(range(12))


class TestThumbnailEngine:
    @staticmethod
    def test_full_renderer_is_reported_as_the_selected_strategy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "part.stl"
        source.write_bytes(b"solid part\nendsolid part\n")
        monkeypatch.setattr(mesh_processing, "_exceeds_cap", lambda *_a, **_k: False)
        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda *_a, **_k: _Mesh())
        monkeypatch.setattr(
            mesh_processing, "_geometry_from_mesh", lambda _mesh: _geometry()
        )
        monkeypatch.setattr(
            "app.services.mesh_render.render_mesh_thumbnail", lambda *_a, **_k: b"png"
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image == b"png"
        assert result.strategy is ThumbnailStrategy.FULL
        assert result.failure_reason is None
        assert result.geometry == _geometry()

    @staticmethod
    def test_large_stl_uses_the_existing_isolated_streamer(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "dense.stl"
        source.write_bytes(b"dense")
        monkeypatch.setattr(mesh_processing, "_exceeds_cap", lambda *_a, **_k: True)
        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda *_a, **_k: None)
        streamed = type(
            "Streamed",
            (),
            {
                "png": b"streamed",
                "bounds_min": (0.0, 0.0, 0.0),
                "bounds_max": (1.0, 2.0, 3.0),
                "triangle_count": 999,
            },
        )()
        monkeypatch.setattr(
            "app.services.stl_streaming.render_stl_preview_isolated",
            lambda *_a, **_k: streamed,
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image == b"streamed"
        assert result.strategy is ThumbnailStrategy.STREAMING
        assert result.complete is True
        assert result.geometry["triangle_count"] == 999

    @staticmethod
    def test_missing_geometry_returns_a_typed_failure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "broken.obj"
        source.write_bytes(b"broken")
        monkeypatch.setattr(mesh_processing, "_exceeds_cap", lambda *_a, **_k: False)
        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda *_a, **_k: None)

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image is None
        assert result.strategy is ThumbnailStrategy.NONE
        assert result.failure_reason is ThumbnailFailureReason.NO_GEOMETRY

    @staticmethod
    def test_post_load_memory_budget_is_enforced(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "oversized.obj"
        source.write_bytes(b"v 0 0 0\n")
        monkeypatch.setattr(mesh_processing, "_exceeds_cap", lambda *_a, **_k: False)
        monkeypatch.setattr(mesh_processing, "_load_mesh", lambda *_a, **_k: _Mesh())
        monkeypatch.setattr(
            mesh_processing, "_geometry_from_mesh", lambda _mesh: _geometry()
        )
        monkeypatch.setattr(mesh_processing, "_ram_triangle_cap", lambda _suffix: 5)
        monkeypatch.setattr(
            "app.services.mesh_render.render_mesh_thumbnail",
            lambda *_a, **_k: pytest.fail(
                "over-budget meshes must not reach the renderer"
            ),
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image is None
        assert result.strategy is ThumbnailStrategy.NONE
        assert result.failure_reason is ThumbnailFailureReason.RESOURCE_LIMIT
