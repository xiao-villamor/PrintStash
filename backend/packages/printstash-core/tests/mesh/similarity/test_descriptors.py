"""Descriptors retain physical meaning and degrade independently of one another."""

from __future__ import annotations

import hashlib
import io
import json
from importlib.resources import files

import numpy as np
import pytest

from printstash_core.mesh import rasterizer, render_geometry
from printstash_core.mesh.similarity import GeometryError, descriptors
from printstash_core.mesh.similarity.geometry import prepare_surface

from ...paths import FIXTURES_DIR

TETRA_CANONICAL_VIEW_HASH = bytes.fromhex(
    "13496cb66b25b269196d30c3669267b614c26b3d95c23a3d40c73f3cc0c72f38194c66936966936d196933c6649e669a"
)


class TestPhysicalDescriptors:
    def test_tetrahedral_physical_descriptors(self, tetra):
        vertices, faces = tetra
        surface = prepare_surface(vertices, faces)
        result = descriptors.describe_surface(
            surface, volume=1000, ambiguous_frame=False
        )

        assert descriptors.hull_volume(surface.vertices) == pytest.approx(1000)
        assert result.hull_ratio == pytest.approx(1)
        assert 0 < result.fill_ratio < 1
        assert 0 < result.inertia_ratios[0] < result.inertia_ratios[1] < 1
        assert result.inertia_ratios[-1] == 1
        assert len(result.sh) == 64
        assert np.linalg.norm(result.sh) == pytest.approx(1)
        assert len(result.sh_basis_digest) == 64
        assert len(result.view_hashes) == 48
        assert result.unavailable == ()

    def test_isotropic_solid_inertia_is_independent_of_orientation(self, cube):
        surface = prepare_surface(*cube)

        result = descriptors.describe_surface(surface, volume=8, ambiguous_frame=True)

        assert result.inertia_ratios == pytest.approx((1, 1, 1))
        assert result.fill_ratio is None
        assert ("fill_ratio", "ambiguous_frame") in result.unavailable
        assert len(result.view_hashes) == 48

    def test_convex_hull_retains_outer_volume(self, cube):
        vertices, _ = cube
        mixed = np.vstack((vertices, [[0.3, -0.2, 0.4], [0, 0, 0]]))[::-1]

        assert descriptors.hull_volume(mixed) == pytest.approx(8)

    def test_coplanar_hull_has_explicit_failure(self):
        with pytest.raises(GeometryError, match="degenerate_hull"):
            descriptors.hull_volume(np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]]))

    def test_hull_point_cap_stops_analysis(self, tetra):
        with pytest.raises(GeometryError, match="hull_resource_limit"):
            descriptors.hull_volume(tetra[0], max_points=1)

    @pytest.mark.parametrize("value", [0, 6_000_001, True])
    def test_rejects_invalid_hull_budget(self, tetra, value):
        with pytest.raises(GeometryError, match="invalid_hull_budget"):
            descriptors.hull_volume(tetra[0], max_points=value)

    def test_invalid_volume_does_not_invent_solid_metrics(self, tetra):
        vertices, faces = tetra

        result = descriptors.describe_surface(
            prepare_surface(vertices, faces[:-1]), volume=None, ambiguous_frame=False
        )

        assert result.hull_ratio is result.fill_ratio is result.inertia_ratios is None
        assert result.sh is not None
        assert set(result.unavailable) == {
            ("hull_ratio", "invalid_volume"),
            ("fill_ratio", "invalid_volume"),
            ("inertia_ratios", "invalid_volume"),
        }

    def test_each_descriptor_failure_preserves_other_results(self, tetra, monkeypatch):
        def unavailable(*args, **kwargs):
            raise GeometryError("resource_limit")

        for operation in ("sh_spectrum", "view_hashes", "hull_volume"):
            monkeypatch.setattr(descriptors, operation, unavailable)

        result = descriptors.describe_surface(
            prepare_surface(*tetra), volume=1000, ambiguous_frame=False
        )

        assert (
            result.sh
            is result.sh_basis_digest
            is result.view_hashes
            is result.hull_ratio
            is None
        )
        assert result.fill_ratio > 0
        assert result.inertia_ratios[-1] == 1
        assert result.unavailable == (
            ("sh", "resource_limit"),
            ("view_hashes", "resource_limit"),
            ("hull_ratio", "resource_limit"),
        )


class TestSphericalHarmonics:
    def test_scaled_translation_has_the_same_spectrum(self, tetra):
        vertices, faces = tetra
        a = descriptors.sh_spectrum(prepare_surface(vertices, faces), fill=True)
        b = descriptors.sh_spectrum(
            prepare_surface(vertices * 25.4 + 321, faces), fill=True
        )

        assert a.shape == (544,)
        np.testing.assert_allclose(a, b, atol=1e-14)
        assert np.isfinite(a).all()
        assert np.count_nonzero(a) > 500

    def test_empty_occupancy_has_explicit_failure(self, tetra, monkeypatch):
        monkeypatch.setattr(
            descriptors, "voxelize", lambda *a, **k: np.zeros((64, 64, 64), bool)
        )

        with pytest.raises(GeometryError, match="empty_occupancy"):
            descriptors.sh_spectrum(prepare_surface(*tetra), fill=True)

    def test_projection_asset_retains_calibration_contract(self):
        package = files("printstash_core.mesh.similarity")
        manifest = json.loads(package.joinpath("sh_basis.json").read_text())
        blob = package.joinpath("sh_basis.npz").read_bytes()
        assert hashlib.sha256(blob).hexdigest() == manifest["sha256"]
        assert not set(manifest["calibration_design_ids"]) & set(
            manifest["evaluation_design_ids"]
        )
        assert manifest["calibration_explained_variance"] > 0.99
        assert manifest["evaluation_relative_reconstruction_mean"] < 0.23
        with np.load(io.BytesIO(blob), allow_pickle=False) as basis:
            components = basis["components"]
            np.testing.assert_allclose(
                components @ components.T, np.eye(64), atol=1e-12
            )
            value, _ = descriptors.project_sh(basis["mean"])
            assert value == (0.0,) * 64

    @pytest.mark.parametrize(
        "corruption",
        [
            "missing",
            "json",
            "encoding",
            "list",
            "digest",
            "recipe",
            "shape",
            "nan",
            "fields",
            "zip",
        ],
    )
    def test_invalid_basis_cannot_be_declared_ready(
        self, tmp_path, monkeypatch, corruption
    ):
        manifest = {"recipe": descriptors.SH_RECIPE}
        data = io.BytesIO()
        mean = np.full(544, np.nan) if corruption == "nan" else np.zeros(544)
        components = np.zeros((1 if corruption == "shape" else 64, 544))
        if corruption == "fields":
            np.savez(data, different=mean)
        else:
            np.savez(data, mean=mean, components=components)
        blob = b"broken zip" if corruption == "zip" else data.getvalue()
        manifest["sha256"] = (
            "wrong" if corruption == "digest" else hashlib.sha256(blob).hexdigest()
        )
        if corruption == "recipe":
            manifest["recipe"] = "old-recipe"
        encoded = {"json": b"{", "encoding": b"\xff", "list": b"[]"}.get(
            corruption, json.dumps(manifest).encode()
        )
        if corruption != "missing":
            (tmp_path / "sh_basis.json").write_bytes(encoded)
            (tmp_path / "sh_basis.npz").write_bytes(blob)
        monkeypatch.setattr(descriptors, "files", lambda package: tmp_path)

        with pytest.raises(GeometryError, match="basis"):
            descriptors.project_sh(np.ones(544))


class TestViews:
    @pytest.mark.parametrize(
        ("shape", "ambiguous_frame", "golden_hex"),
        [
            pytest.param(
                "tetra",
                False,
                "13496cb66b25b269196d30c3669267b614c26b3d95c23a3d40c73f3cc0c72f38194c66936966936d196933c6649e669a",
                id="tetra-canonical",
            ),
            pytest.param(
                "tetra",
                True,
                "4c38659663c732c7196936c664966f9215696a94956b7a9441913e6ec5946a6b4c30669b63ce31ce196933c6649e669a",
                id="tetra-ambiguous",
            ),
            pytest.param(
                "cube",
                False,
                "49793e97606cb44649863f686093b5b9446e3a934bcc946e5091276e5973d691433d2d673a993498433c38e266c96d99",
                id="cube-canonical",
            ),
            pytest.param(
                "cube",
                True,
                "569a3b8530e4b36556c52fda6491e232149c6bd86bc2946b149c6bd86bc2946b462d39d42dc4297d462939d629d2297d",
                id="cube-ambiguous",
            ),
        ],
    )
    def test_preserves_legacy_view_hash_golden(
        self, request, shape, ambiguous_frame, golden_hex
    ):
        vertices, faces = request.getfixturevalue(shape)
        baseline = json.loads((FIXTURES_DIR / "render-prepared-v1.json").read_text())
        reference = next(
            row
            for row in baseline["descriptors"]
            if row["name"] == shape and row["ambiguous_frame"] is ambiguous_frame
        )

        result = descriptors.view_hashes(
            prepare_surface(vertices, faces), ambiguous_frame=ambiguous_frame
        )

        assert (
            baseline["provenance"]["source_commit"]
            == "59e91d25172ca53c47f26b1ec773992defddc57a"
        )
        assert (
            hashlib.sha256(vertices.tobytes()).hexdigest()
            == reference["vertices_sha256"]
        )
        assert hashlib.sha256(faces.tobytes()).hexdigest() == reference["faces_sha256"]
        assert reference["view_hashes_hex"] == golden_hex
        assert result == bytes.fromhex(golden_hex)
        assert len(result) == 48

    def test_view_hashes_survive_equivalent_exports(self, tetra):
        vertices, faces = tetra

        a = descriptors.view_hashes(
            prepare_surface(vertices, faces), ambiguous_frame=False
        )
        b = descriptors.view_hashes(
            prepare_surface(vertices[::-1] * 2, 3 - faces[::-1]), ambiguous_frame=False
        )

        assert a == b
        assert len(set(a[i : i + 8] for i in range(0, 48, 8))) > 1

    def test_reuses_preparation_for_six_views(self, tetra, monkeypatch):
        surface = prepare_surface(*tetra)
        preparations = []
        renders = []
        prepare = render_geometry.prepare_mesh_render
        render = rasterizer.render_prepared_pixels

        def observe_preparation(mesh, *args, **kwargs):
            prepared = prepare(mesh, *args, **kwargs)
            preparations.append(prepared)
            return prepared

        def observe_render(prepared, name, *args, **kwargs):
            renders.append((prepared, name, kwargs))
            return render(prepared, name, *args, **kwargs)

        monkeypatch.setattr(
            descriptors, "prepare_mesh_render", observe_preparation, raising=False
        )
        monkeypatch.setattr(
            descriptors, "render_prepared_pixels", observe_render, raising=False
        )

        result = descriptors.view_hashes(surface, ambiguous_frame=False)

        assert result == TETRA_CANONICAL_VIEW_HASH
        assert len(preparations) == 1
        assert len(renders) == 6
        assert all(prepared is preparations[0] for prepared, _, _ in renders)
        assert {name for _, name, _ in renders} == {"geometry-descriptor"}
        assert {
            (options["width"], options["height"], options["matte"])
            for _, _, options in renders
        } == {(64, 64, True)}
        np.testing.assert_array_equal(
            np.stack([options["view_rotation"] for _, _, options in renders]),
            np.array(
                [
                    [[0, -1, 0], [0, 0, 1], [-1, 0, 0]],
                    [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
                    [[0, 0, -1], [1, 0, 0], [0, -1, 0]],
                    [[0, 0, 1], [1, 0, 0], [0, 1, 0]],
                    [[-1, 0, 0], [0, 1, 0], [0, 0, -1]],
                    [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                ]
            ),
        )

    @pytest.mark.parametrize(
        "ambiguous_frame", [False, True], ids=["canonical", "ambiguous"]
    )
    def test_keeps_view_source_arrays_unchanged(self, tetra, ambiguous_frame):
        surface = prepare_surface(*tetra)
        vertices, faces, frame = (
            surface.vertices.copy(),
            surface.faces.copy(),
            surface.frame.copy(),
        )
        surface.vertices.flags.writeable = False
        surface.faces.flags.writeable = False
        surface.frame.flags.writeable = False

        result = descriptors.view_hashes(surface, ambiguous_frame=ambiguous_frame)

        assert len(result) == 48
        np.testing.assert_array_equal(surface.vertices, vertices)
        np.testing.assert_array_equal(surface.faces, faces)
        np.testing.assert_array_equal(surface.frame, frame)

    def test_hashes_actual_pixels_without_codec_roundtrip(self, tetra, monkeypatch):
        from PIL import Image

        surface = prepare_surface(*tetra)

        def reject_codec(*_args, **_kwargs):
            raise AssertionError(
                "view descriptors must consume actual pixels without an image codec"
            )

        monkeypatch.setattr(Image, "open", reject_codec)
        monkeypatch.setattr(Image.Image, "save", reject_codec)

        result = descriptors.view_hashes(surface, ambiguous_frame=False)

        assert result == TETRA_CANONICAL_VIEW_HASH
        assert len(set(result[index : index + 8] for index in range(0, 48, 8))) > 1

    def test_preserves_legacy_grayscale_alpha_policy(self, tetra, monkeypatch):
        surface = prepare_surface(*tetra)
        backgrounds = []
        convert_rgb = rasterizer.RenderedPixels.rgb

        def observe_background(pixels, background):
            backgrounds.append(background)
            return convert_rgb(pixels, background)

        monkeypatch.setattr(rasterizer.RenderedPixels, "rgb", observe_background)

        result = descriptors.view_hashes(surface, ambiguous_frame=False)

        assert result == TETRA_CANONICAL_VIEW_HASH
        assert backgrounds == [rasterizer.RGBBackground.IGNORE_ALPHA] * 6

    def test_missing_preparation_has_explicit_reason(self, tetra, monkeypatch):
        monkeypatch.setattr(descriptors, "prepare_mesh_render", lambda *a, **k: None)

        with pytest.raises(GeometryError) as raised:
            descriptors.view_hashes(prepare_surface(*tetra), ambiguous_frame=False)

        assert raised.value.code == "view_render_unavailable"

    def test_renderer_failure_has_explicit_reason(self, tetra, monkeypatch):
        monkeypatch.setattr(
            descriptors, "render_prepared_pixels", lambda *a, **k: None, raising=False
        )

        with pytest.raises(GeometryError, match="view_render_unavailable"):
            descriptors.view_hashes(prepare_surface(*tetra), ambiguous_frame=False)

    def test_dct_distinguishes_orthogonal_edges(self):
        pixels = np.zeros((64, 64))
        pixels[:24] = 255

        assert descriptors.dct_hash(pixels) != descriptors.dct_hash(pixels.T)
        assert len(descriptors.dct_hash(pixels)) == 8

    @pytest.mark.parametrize("image", [np.zeros((32, 64)), np.full((64, 64), np.nan)])
    def test_invalid_view_is_not_hashed(self, image):
        with pytest.raises(GeometryError, match="invalid_view_image"):
            descriptors.dct_hash(image)
