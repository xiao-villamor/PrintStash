"""A persistent backend receives prepared geometry before canonical encoding."""

import numpy as np

from printstash_core.mesh.rasterizer import render_prepared_pixels
from printstash_core.mesh.render_geometry import PreparedRender


class Backend:
    def __init__(self):
        self.frames = []

    def __call__(self, img, zbuf, tri, vert_nrm, shade, base_color, width, height):
        raise AssertionError("Prepared backends must not receive duplicate face chunks")

    def draw_prepared(self, geometry, rotation, screen, width, height, matte):
        self.frames.append((geometry, width, height, matte))

    def finish(self, img, zbuf):
        img[:] = 100
        zbuf[:] = 0


class TestPreparedRasterizer:
    def test_keeps_common_frame_processing(self):
        vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
        geometry = PreparedRender(
            vertices,
            1,
            lambda size: iter([np.asarray([[0, 1, 2]])]),
            np.arange(3),
            np.tile([0.0, 0.0, 1.0], (3, 1)),
        )
        backend = Backend()

        result = render_prepared_pixels(
            geometry, "", 32, 32, rasterise_triangles=backend, matte=True
        )

        assert result.width == result.height == 32
        assert len(backend.frames) == 1
        assert backend.frames[0][0] is geometry
        assert backend.frames[0][3] is True
        assert set(result.rgba[3::4]) == {255}
        assert min(result.rgba[0::4]) < max(result.rgba[0::4]) <= 100
