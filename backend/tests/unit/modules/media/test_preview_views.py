"""Every shape uses the same source-relative oblique cameras; poles are optional."""

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

from app.modules.media.preview_views import candidates, render_views

# The shared default, VAULT_MESH_MAX_RENDER_TRIANGLES.
LIMIT = 2_000_000


class TestCandidates:
    def test_orbits_share_the_cover_camera_elevation(self):
        _, views = candidates(trimesh.creation.box(), max_faces=LIMIT)

        assert [view.elevation_degrees for view in views[:4]] == [
            PREVIEW_PROFILE.hero_elevation_degrees
        ] * 4
        assert np.asarray(views[0].rotation)[2, 2] == pytest.approx(
            -np.sin(np.radians(PREVIEW_PROFILE.hero_elevation_degrees))
        )

    def test_preserves_orbit_order(self):
        mesh = trimesh.creation.box(extents=(10, 12, 14))
        profile, views = candidates(mesh, max_faces=LIMIT)
        assert profile == "orbit"
        assert [v.azimuth_degrees for v in views[:4]] == [-35, 55, 145, 235]
        assert [v.role for v in views] == ["orbit"] * 4

    def test_frames_a_rotated_plate(self):
        mesh = trimesh.creation.box(extents=(30, 20, 1))
        rotation = trimesh.transformations.rotation_matrix(0.8, (1, 2, 3))
        mesh.apply_transform(rotation)
        profile, views = candidates(mesh, max_faces=LIMIT)
        assert profile == "orbit"
        assert [v.id for v in views] == [f"orbit-{i}" for i in range(4)]

    @pytest.mark.parametrize("extents", [(30, 20, 1), (10, 12, 14)])
    def test_adds_poles_only_when_requested(self, extents):
        profile, views = candidates(
            trimesh.creation.box(extents=extents), max_faces=LIMIT, include_poles=True
        )
        assert profile == "orbit"
        assert [view.id for view in views] == [
            "orbit-0",
            "orbit-1",
            "orbit-2",
            "orbit-3",
            "top",
            "bottom",
        ]

    def test_distinguishes_a_rod(self):
        profile, _ = candidates(
            trimesh.creation.box(extents=(1, 1, 30)), max_faces=LIMIT
        )
        assert profile == "orbit"

    def test_preserves_imported_frame_when_ambiguous(self):
        mesh = trimesh.creation.icosphere(subdivisions=1)
        profile, views = candidates(mesh, max_faces=LIMIT)
        assert profile == "orbit"
        assert candidates(mesh, max_faces=LIMIT) == (profile, views)

    def test_keeps_opposite_candidates(self):
        _, views = candidates(
            trimesh.creation.box(extents=(20, 20, 1)), max_faces=LIMIT
        )
        assert [v.role for v in views] == ["orbit"] * 4


class TestRenderViews:
    def test_rejects_invalid_advertised_image(self, monkeypatch):
        monkeypatch.setattr(
            "app.modules.media.preview_views.render_prepared_thumbnail",
            lambda *args, **kwargs: b"not an image",
        )
        with pytest.raises(ValueError, match="thumbnail"):
            render_views(
                trimesh.creation.box(), source_sha256="a" * 64, max_faces=LIMIT
            )

    def test_records_source_relative_projection(self):
        mesh = trimesh.creation.box()
        mesh.apply_translation([10, 20, 30])
        manifest, images = render_views(mesh, source_sha256="a" * 64, max_faces=LIMIT)
        assert manifest.source_sha256 == "a" * 64
        assert manifest.center == (10, 20, 30)
        assert len(images) == len(manifest.views) == 4
        for view, image in zip(manifest.views, images, strict=True):
            assert view.width == 480
            assert view.height == 360
            assert view.size == len(image)
            assert view.projection == "orthographic"
            assert view.pixels_per_unit > 0


class TestLimits:
    def test_reports_face_limits_as_resource_limits(self):
        from app.modules.media.preview_contracts import PreviewLimitError

        with pytest.raises(PreviewLimitError):
            candidates(trimesh.creation.box(), max_faces=11)

    def test_reports_oversized_images_as_resource_limits(self, monkeypatch):
        from app.modules.media.preview_contracts import PreviewLimitError

        monkeypatch.setattr("app.modules.media.preview_views.MAX_IMAGE_BYTES", 10)
        with pytest.raises(PreviewLimitError):
            render_views(
                trimesh.creation.box(), source_sha256="a" * 64, max_faces=LIMIT
            )


class TestDegenerateViews:
    # A plate standing on edge: looking straight down sees less than a pixel.
    PLATE = (10, 0.01, 10)

    def test_omits_a_pole_that_sees_only_an_edge(self):
        manifest, images = render_views(
            trimesh.creation.box(extents=self.PLATE),
            source_sha256="a" * 64,
            max_faces=LIMIT,
            include_poles=True,
        )
        assert [view.id for view in manifest.views] == [
            "orbit-0",
            "orbit-1",
            "orbit-2",
            "orbit-3",
        ]
        assert manifest.size == sum(len(image) for image in images)

    def test_reports_progress_for_every_attempted_view(self):
        reported = []
        render_views(
            trimesh.creation.box(extents=self.PLATE),
            source_sha256="a" * 64,
            max_faces=LIMIT,
            include_poles=True,
            progress=lambda done, total: reported.append((done, total)),
        )
        assert reported == [(done, 6) for done in range(1, 7)]

    def test_fails_when_an_orbit_view_is_empty(self, monkeypatch):
        from app.modules.media import thumbnail

        def empty(*args, **kwargs):
            raise thumbnail.EmptyThumbnailError("thumbnail_empty")

        monkeypatch.setattr(thumbnail, "to_webp", empty)
        with pytest.raises(thumbnail.EmptyThumbnailError):
            render_views(
                trimesh.creation.box(), source_sha256="a" * 64, max_faces=LIMIT
            )
