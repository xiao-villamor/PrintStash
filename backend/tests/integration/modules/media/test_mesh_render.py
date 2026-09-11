"""The full repository Benchy retains its preview when render allocation changes."""

import io

import numpy as np
from PIL import Image

from app.modules.media import mesh_processing, mesh_render
from tests.paths import FIXTURES_DIR, TESTDATA_DIR


class TestMeshRender:
    def test_preserves_preview_pixels(self):
        mesh = mesh_processing._load_mesh(TESTDATA_DIR / "benchy/3dbenchy.stl")

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
