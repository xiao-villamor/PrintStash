"""Camera selection is surface weighted, source relative, and deterministic."""

import numpy as np
import pytest
import trimesh

from app.modules.media.preview_views import candidates, render_views


class TestCandidates:
    def test_preserves_orbit_order(self):
        mesh = trimesh.creation.box(extents=(10, 12, 14))
        profile, views = candidates(mesh)
        assert profile == "orbit"
        assert [v.azimuth_degrees for v in views[:4]] == [-35, 55, 145, 235]
        assert [v.role for v in views] == ["orbit"] * 4 + ["top", "bottom"]

    def test_frames_a_rotated_plate(self):
        mesh = trimesh.creation.box(extents=(30, 20, 1))
        rotation = trimesh.transformations.rotation_matrix(0.8, (1, 2, 3))
        mesh.apply_transform(rotation)
        profile, views = candidates(mesh)
        assert profile == "planar"
        normal = rotation[:3, 2]
        assert abs(np.dot(np.asarray(views[0].rotation)[2], normal)) > 0.9

    def test_distinguishes_a_rod(self):
        profile, _ = candidates(trimesh.creation.box(extents=(1, 1, 30)))
        assert profile == "orbit"

    def test_preserves_imported_frame_when_ambiguous(self):
        mesh = trimesh.creation.icosphere(subdivisions=1)
        profile, views = candidates(mesh)
        assert profile == "orbit"
        assert candidates(mesh) == (profile, views)

    def test_keeps_opposite_candidates(self):
        _, views = candidates(trimesh.creation.box(extents=(20, 20, 1)))
        assert [v.role for v in views] == ["face", "reverse", "edge", "top", "bottom"]


class TestRenderViews:
    def test_rejects_invalid_advertised_image(self, monkeypatch):
        monkeypatch.setattr(
            "app.modules.media.preview_views.render_prepared_thumbnail",
            lambda *args, **kwargs: b"not an image",
        )
        with pytest.raises(ValueError, match="thumbnail"):
            render_views(trimesh.creation.box(), source_sha256="a" * 64)

    def test_records_source_relative_projection(self):
        mesh = trimesh.creation.box()
        mesh.apply_translation([10, 20, 30])
        manifest, images = render_views(mesh, source_sha256="a" * 64)
        assert manifest.source_sha256 == "a" * 64
        assert manifest.center == (10, 20, 30)
        assert len(images) == len(manifest.views) == 6
        for view, image in zip(manifest.views, images, strict=True):
            assert view.width == 480
            assert view.height == 360
            assert view.size == len(image)
            assert view.projection == "orthographic"
            assert view.pixels_per_unit > 0


class TestLimits:
    def test_reports_face_limits_as_resource_limits(self, monkeypatch):
        from app.modules.media.preview_contracts import PreviewLimitError

        monkeypatch.setattr("app.modules.media.preview_views.MAX_FACES", 11)
        with pytest.raises(PreviewLimitError):
            candidates(trimesh.creation.box())

    def test_reports_oversized_images_as_resource_limits(self, monkeypatch):
        from app.modules.media.preview_contracts import PreviewLimitError

        monkeypatch.setattr("app.modules.media.preview_views.MAX_IMAGE_BYTES", 10)
        with pytest.raises(PreviewLimitError):
            render_views(trimesh.creation.box(), source_sha256="a" * 64)
