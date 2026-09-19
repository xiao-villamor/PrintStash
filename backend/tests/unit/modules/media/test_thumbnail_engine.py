"""Defend bounded strategy selection in the thumbnail orchestration seam.

These tests keep dense or invalid meshes from bypassing resource limits while
preserving the isolated renderer that fixed issue #67.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.media import mesh_processing
from app.modules.media.thumbnail_engine import (
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
    @pytest.mark.parametrize("cap", [99, 2_000_001, True, 100.0])
    def test_rejects_invalid_analysis_budgets(self, tmp_path, cap):
        with pytest.raises(ValueError, match="invalid_triangle_cap"):
            ThumbnailEngine().generate(
                ThumbnailRequest(tmp_path / "unused.stl", triangle_cap=cap)
            )

    @staticmethod
    def test_stl_path_renderer_avoids_copying_loaded_mesh_buffers(
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
            "app.modules.media.mesh_render.render_mesh_thumbnail",
            lambda *_a, **_k: pytest.fail("STL preview must use the path command"),
        )
        streamed = type(
            "Streamed",
            (),
            {
                "png": b"png",
                "bounds_min": (0.0, 0.0, 0.0),
                "bounds_max": (10.0, 20.0, 30.0),
                "triangle_count": 12,
            },
        )()
        monkeypatch.setattr(
            "app.modules.media.stl_streaming.render_stl_preview_isolated",
            lambda *_a, **_k: streamed,
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image == b"png"
        assert result.strategy is ThumbnailStrategy.STREAMING
        assert result.failure_reason is None
        assert result.geometry == _geometry()

    @staticmethod
    def test_stl_path_failure_retains_the_full_renderer_fallback(
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
            "app.modules.media.stl_streaming.render_stl_preview_isolated",
            lambda *_a, **_k: None,
        )
        monkeypatch.setattr(
            "app.modules.media.mesh_render.render_mesh_thumbnail",
            lambda *_a, **_k: b"fallback",
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image == b"fallback"
        assert result.strategy is ThumbnailStrategy.FULL

    @staticmethod
    def test_full_renderer_exception_returns_a_typed_failure(
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
            "app.modules.media.stl_streaming.render_stl_preview_isolated",
            lambda *_a, **_k: None,
        )

        def fail_render(*_args, **_kwargs):
            raise RuntimeError("render failed")

        monkeypatch.setattr(
            "app.modules.media.mesh_render.render_mesh_thumbnail", fail_render
        )
        monkeypatch.setattr(
            "app.modules.media.stl_fallback.render_stl_thumbnail",
            lambda *_a, **_k: None,
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image is None
        assert result.strategy is ThumbnailStrategy.NONE
        assert result.failure_reason is ThumbnailFailureReason.RENDERER_NO_OUTPUT

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
            "app.modules.media.stl_streaming.render_stl_preview_isolated",
            lambda *_a, **_k: streamed,
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image == b"streamed"
        assert result.strategy is ThumbnailStrategy.STREAMING
        assert result.complete is True
        assert result.geometry["triangle_count"] == 999

    @staticmethod
    def test_thumbnail_only_stl_does_not_allocate_a_trimesh(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "repair.stl"
        source.write_bytes(b"solid part\nendsolid part\n")
        monkeypatch.setattr(mesh_processing, "_exceeds_cap", lambda *_a, **_k: False)
        monkeypatch.setattr(
            mesh_processing,
            "_load_mesh",
            lambda *_a, **_k: pytest.fail("thumbnail-only STL must stay path based"),
        )
        streamed = type(
            "Streamed",
            (),
            {
                "png": b"streamed",
                "bounds_min": (0.0, 0.0, 0.0),
                "bounds_max": (1.0, 2.0, 3.0),
                "triangle_count": 4,
            },
        )()
        monkeypatch.setattr(
            "app.modules.media.stl_streaming.render_stl_preview_isolated",
            lambda *_a, **_k: streamed,
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path=source, include_geometry=False)
        )

        assert result.image == b"streamed"
        assert result.strategy is ThumbnailStrategy.STREAMING

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
            "app.modules.media.mesh_render.render_mesh_thumbnail",
            lambda *_a, **_k: pytest.fail(
                "over-budget meshes must not reach the renderer"
            ),
        )

        result = ThumbnailEngine().generate(ThumbnailRequest(path=source))

        assert result.image is None
        assert result.strategy is ThumbnailStrategy.NONE
        assert result.failure_reason is ThumbnailFailureReason.RESOURCE_LIMIT
