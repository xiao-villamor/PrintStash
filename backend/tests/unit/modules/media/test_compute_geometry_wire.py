"""Geometry framing shares identity and limits with the device decoder."""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest
from printstash_core.mesh.render_geometry import PreparedRenderGeometry

from app.modules.media.compute_geometry import decode, encode, split
from app.modules.media.compute_geometry_wire import descriptor, identity
from app.modules.media.gpu_normals import VisualRender, canonical, defer


@pytest.fixture
def geometry():
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    return PreparedRenderGeometry(vertices, 1, lambda size: iter([faces]))


class TestGeometryWire:
    @pytest.mark.parametrize("mode", ["visual", "deferred", "canonical"], ids=str)
    def test_preserves_geometry_identity(self, geometry, mode):
        prepared = VisualRender(geometry)
        if mode == "deferred":
            prepared = defer(geometry)
        elif mode == "canonical":
            prepared = canonical(prepared)
        payload = encode(prepared, 64, 48, [None], False)
        key, header, body = split(payload)
        values, layout = descriptor(header.encode())
        assert key == identity(layout, body)
        assert (values["width"], values["height"], values["faces"]) == (64, 48, 1)
        restored, *_ = decode(payload)
        np.testing.assert_array_equal(
            canonical(restored).smooth_normals, canonical(prepared).smooth_normals
        )
        if mode == "visual":
            assert isinstance(restored, VisualRender)
            assert not hasattr(restored, "position_ids")
            assert not hasattr(restored, "smooth_normals")
            assert len(body) == 48
            assert not restored.vertices.flags.writeable

    @pytest.mark.parametrize(
        "fault",
        ["short", "header", "body", "count", "mode"],
        ids=str,
    )
    def test_refuses_malformed_geometry_framing(self, geometry, fault):
        payload = encode(VisualRender(geometry), 64, 48, [None], False)
        size = struct.unpack_from("!I", payload)[0]
        header = json.loads(payload[4 : 4 + size])
        if fault == "short":
            payload = payload[:2]
        elif fault == "header":
            payload = struct.pack("!I", 4097) + payload[4:]
        elif fault == "body":
            payload = payload[:-1]
        else:
            if fault == "count":
                header["normals"] = 2
            else:
                header["normal_mode"] = "future-unqualified"
            value = json.dumps(header).encode()
            payload = struct.pack("!I", len(value)) + value + payload[4 + size :]
        with pytest.raises(ValueError, match="compute_"):
            decode(payload)

    @pytest.mark.parametrize("fault", ["position", "range", "face"], ids=str)
    def test_owner_validates_framed_arrays(self, geometry, fault):
        payload = bytearray(encode(VisualRender(geometry), 64, 48, [None], False))
        start = 4 + struct.unpack_from("!I", payload)[0]
        if fault == "face":
            struct.pack_into("<I", payload, start + 36, 99)
        else:
            struct.pack_into(
                "<f", payload, start, float("nan") if fault == "position" else 1e30
            )
        _key, header, _body = split(bytes(payload))
        values, _layout = descriptor(header.encode())
        assert values["faces"] == 1
        with pytest.raises(ValueError, match="compute_"):
            decode(bytes(payload))
