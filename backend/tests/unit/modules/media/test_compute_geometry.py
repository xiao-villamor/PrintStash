"""The compute wire format admits bounded arrays and rejects malformed geometry."""

import struct

import numpy as np
import pytest
from printstash_core.mesh.render_geometry import prepare_mesh_render

from app.modules.media.compute_geometry import decode, encode


class TestDecode:
    def test_preserves_prepared_geometry(self):
        # A structural object keeps the codec independent of native parsers.
        from types import SimpleNamespace

        mesh = SimpleNamespace(
            vertices=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
            faces=np.array([[0, 1, 2]]),
        )
        prepared = prepare_mesh_render(mesh)
        payload = encode(prepared, 64, 64, [None], False)

        result, width, height, views, matte = decode(payload)

        np.testing.assert_array_equal(result.vertices, prepared.vertices)
        assert (width, height, views, matte) == (64, 64, [None], False)

    def test_rejects_truncated_arrays(self):
        with pytest.raises(ValueError, match="compute_geometry"):
            decode(struct.pack("!I", 4097))

    def test_rejects_oversized_headers(self):
        with pytest.raises(ValueError, match="compute_geometry_header"):
            decode(struct.pack("!I", 4097) + b"{}")

    @pytest.mark.parametrize("header", [b"null", b"1", b"[]", b"[{}]"])
    def test_rejects_non_object_descriptors(self, header):
        from app.modules.media.compute_geometry import decode_header

        with pytest.raises(ValueError, match="compute_geometry_header"):
            decode_header(header)

    @pytest.mark.parametrize(
        "camera", [{}, "invalid", [1, 2], [[float("inf")] * 3] * 3]
    )
    def test_rejects_malformed_cameras(self, camera):
        import json

        from app.modules.media.compute_geometry import decode_header

        header = json.dumps(
            {
                "vertices": 3,
                "faces": 1,
                "normals": 3,
                "width": 64,
                "height": 48,
                "views": [camera],
                "matte": False,
            }
        ).encode()
        with pytest.raises(ValueError, match="compute_camera_invalid"):
            decode_header(header)
