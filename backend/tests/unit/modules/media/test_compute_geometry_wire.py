"""Staged thumbnail framing shares identity and limits with the device decoder."""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest
from printstash_core.mesh.render_geometry import PreparedRenderGeometry

from app.modules.media.compute_geometry import decode, encode, split
from app.modules.media.compute_geometry_wire import split_thumbnail
from app.modules.media.gpu_normals import VisualRender, canonical, defer


@pytest.fixture
def geometry():
    vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    return PreparedRenderGeometry(vertices, 1, lambda size: iter([faces]))


class TestSplitThumbnail:
    @pytest.mark.parametrize("mode", ["visual", "deferred", "canonical"], ids=str)
    def test_roundtrips_owned_staging_without_array_copy(self, geometry, mode):
        prepared = VisualRender(geometry)
        if mode == "deferred":
            prepared = defer(geometry)
        elif mode == "canonical":
            prepared = canonical(prepared)
        payload = encode(prepared, 64, 48, [None], False)
        wire = split_thumbnail(memoryview(payload))
        key, header, body = split(payload)
        assert wire.body.obj is payload
        assert (wire.geometry_key, wire.header, bytes(wire.body)) == (key, header, body)
        assert (wire.width, wire.height, wire.face_count) == (64, 48, 1)
        restored, *_ = decode(payload)
        np.testing.assert_array_equal(
            canonical(restored).smooth_normals, canonical(prepared).smooth_normals
        )
        if mode == "visual":
            assert isinstance(restored, VisualRender)
            assert not hasattr(restored, "position_ids")
            assert not hasattr(restored, "smooth_normals")
            assert len(wire.body) == 48
            assert not restored.vertices.flags.writeable

    @pytest.mark.parametrize(
        "fault",
        ["short", "header", "body", "camera", "matte", "count", "mode"],
        ids=str,
    )
    def test_refuses_malformed_staging_before_exchange(self, geometry, fault):
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
            if fault == "camera":
                header["views"] = [None, None]
            elif fault == "matte":
                header["matte"] = True
            elif fault == "count":
                header["normals"] = 2
            else:
                header["normal_mode"] = "future-unqualified"
            value = json.dumps(header).encode()
            payload = struct.pack("!I", len(value)) + value + payload[4 + size :]
        with pytest.raises(ValueError, match="compute_"):
            split_thumbnail(payload)

    @pytest.mark.parametrize("fault", ["position", "range", "face"], ids=str)
    def test_owner_still_validates_each_array_after_staging(self, geometry, fault):
        payload = bytearray(encode(VisualRender(geometry), 64, 48, [None], False))
        start = 4 + struct.unpack_from("!I", payload)[0]
        if fault == "face":
            struct.pack_into("<I", payload, start + 36, 99)
        else:
            struct.pack_into(
                "<f", payload, start, float("nan") if fault == "position" else 1e30
            )
        wire = split_thumbnail(payload)
        assert wire.face_count == 1
        with pytest.raises(ValueError, match="compute_"):
            decode(bytes(payload))
