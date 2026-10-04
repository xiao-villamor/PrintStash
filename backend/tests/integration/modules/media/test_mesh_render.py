"""The full repository Benchy retains its preview when render allocation changes."""

import io

import numpy as np
import pytest
import trimesh
from PIL import Image
from printstash_core.mesh.similarity.components import compose_scene

from app.core.config import settings
from app.modules.media import mesh_loading, mesh_render
from app.modules.media.three_mf_scene import read_scene
from tests.factories.geometry import tetrahedron, three_mf
from tests.paths import FIXTURES_DIR, TESTDATA_DIR


class TestMeshRender:
    def test_preserves_preview_pixels(self):
        mesh = mesh_loading.load_mesh(TESTDATA_DIR / "benchy/3dbenchy.stl")

        image = mesh_render.render_mesh_thumbnail(mesh, "benchy", width=640, height=480)

        assert image is not None
        with (
            Image.open(io.BytesIO(image)) as actual,
            Image.open(FIXTURES_DIR / "benchy-preview.png") as expected,
        ):
            assert actual.size == expected.size == (640, 480)
            rendered = np.asarray(actual).astype(float)
            reference = np.asarray(expected).astype(float)
            np.testing.assert_array_equal(rendered[..., 3], reference[..., 3])
            # Compare displayed colors on both theme extremes. Lanczos stores
            # unpremultiplied RGB: a 22-level channel difference at alpha=11
            # changes the visible pixel by less than one level. Raw RGB alone
            # overstates rounding in nearly transparent silhouette pixels.
            for background in (0, 255):
                alpha = rendered[..., 3:] / 255
                np.testing.assert_allclose(
                    rendered[..., :3] * alpha + background * (1 - alpha),
                    reference[..., :3] * alpha + background * (1 - alpha),
                    atol=1,
                )

    def test_preserves_scene_preview_pixels(self, tmp_path):
        path = tmp_path / "plate.3mf"
        path.write_bytes(three_mf(build=((1, None),) * 64))
        scene = read_scene(path)
        vertices, faces = compose_scene(scene)
        expected = mesh_render.render_mesh_thumbnail(
            trimesh.Trimesh(vertices=vertices, faces=faces, process=False),
            "placed",
            width=160,
            height=120,
        )

        actual = mesh_render.render_scene_thumbnail(
            scene, "placed", width=160, height=120
        )

        assert expected is not None
        assert actual is not None
        np.testing.assert_array_equal(
            np.asarray(Image.open(io.BytesIO(actual))),
            np.asarray(Image.open(io.BytesIO(expected))),
        )


class TestPreparedMeshRender:
    @pytest.mark.parametrize("output_format", ["PNG", "WEBP"])
    def test_preserves_legacy_encoded_thumbnail(self, output_format):
        mesh = tetrahedron()
        original = mesh.vertices.tobytes(), mesh.faces.tobytes()
        expected = mesh_render.render_mesh_thumbnail(
            mesh, "part", width=64, height=48, output_format=output_format
        )

        prepared = mesh_render.prepare_mesh_render(mesh)
        assert prepared is not None
        actual = mesh_render.render_prepared_thumbnail(
            prepared, "part", width=64, height=48, output_format=output_format
        )

        assert actual is not None
        assert actual == expected
        assert (mesh.vertices.tobytes(), mesh.faces.tobytes()) == original

    def test_preserves_legacy_white_rgb(self, monkeypatch):
        from printstash_core.mesh import rasterizer
        from printstash_core.mesh.rasterizer import RGBBackground

        mesh = tetrahedron()
        frame = np.eye(3)
        encoded = rasterizer.render_mesh_thumbnail(
            mesh, "", width=32, height=32, view_rotation=frame, matte=True
        )
        assert encoded is not None
        with Image.open(io.BytesIO(encoded)) as image:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            background.alpha_composite(rgba)
            expected = background.convert("RGB").tobytes()
        calls = []
        render = rasterizer.render_prepared_pixels

        def observed(*args, **kwargs):
            calls.append(kwargs)
            return render(*args, **kwargs)

        monkeypatch.setattr(rasterizer, "render_prepared_pixels", observed)
        prepared = mesh_render.prepare_mesh_render(mesh)
        assert prepared is not None
        pixels = mesh_render.render_prepared_pixels(
            prepared, "", width=32, height=32, view_rotation=frame, matte=True
        )

        assert pixels is not None
        assert pixels.width == pixels.height == 32
        assert pixels.rgb(RGBBackground.WHITE) == expected
        assert len(calls) == 1
        assert calls[0]["face_chunk_size"] == settings.mesh_render_face_chunk_size
        assert calls[0]["logger"] is mesh_render.logger
        assert calls[0]["rasterise_triangles"] is mesh_render._rasterise_triangles

    def test_preserves_retained_scene_preview(self, tmp_path):
        path = tmp_path / "shared.3mf"
        path.write_bytes(three_mf(build=((1, None),) * 16))
        scene = read_scene(path)
        original = tuple(
            (resource.vertices.tobytes(), resource.faces.tobytes())
            for resource in scene.resources
        )
        transforms = tuple(instance.transform.tobytes() for instance in scene.instances)
        expected = mesh_render.render_scene_thumbnail(scene, "", width=64, height=48)

        prepared = mesh_render.prepare_scene_render(scene)
        actual = mesh_render.render_prepared_thumbnail(
            prepared, "", width=64, height=48
        )

        assert actual is not None
        assert actual == expected
        assert (
            tuple(
                (resource.vertices.tobytes(), resource.faces.tobytes())
                for resource in scene.resources
            )
            == original
        )
        assert (
            tuple(instance.transform.tobytes() for instance in scene.instances)
            == transforms
        )
