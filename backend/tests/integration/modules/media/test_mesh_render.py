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
            # One channel level accommodates native floating-point variation;
            # geometry, silhouette and lighting remain pixel-aligned.
            np.testing.assert_allclose(
                np.asarray(actual).astype(int), np.asarray(expected).astype(int), atol=1
            )
