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


@pytest.fixture
def deferred_geometry():
    from types import SimpleNamespace

    from printstash_core.mesh.render_geometry import prepare_mesh

    from app.modules.media.gpu_normals import defer

    mesh = SimpleNamespace(
        vertices=np.asarray([[0.0, 0, 0], [10, 0, 0], [0, 10, 0]]),
        faces=np.asarray([[0, 1, 2]], dtype=np.int64),
    )
    return defer(prepare_mesh(mesh))


def rewrite_header(payload, changes):
    import json

    size = struct.unpack("!I", payload[:4])[0]
    header = json.loads(payload[4 : 4 + size])
    for key, value in changes.items():
        if value is ...:
            del header[key]
        else:
            header[key] = value
    encoded = json.dumps(header).encode()
    return struct.pack("!I", len(encoded)) + encoded + payload[4 + size :]


class TestDeferredCodec:
    def test_preserves_deferred_wire_ownership(self, deferred_geometry):
        from app.modules.media.compute_geometry import body_size, decode_header, split
        from app.modules.media.gpu_normals import NORMAL_MODE, DeferredRender, canonical

        payload = encode(deferred_geometry, 64, 64, [None], False)
        key, header, body = split(payload)
        layout, *_ = decode_header(header.encode())
        result, *_ = decode(payload)
        assert isinstance(result, DeferredRender)
        assert layout.normal_mode == NORMAL_MODE
        assert len(body) == body_size(layout)
        assert not hasattr(result, "smooth_normals")
        faces = next(result.face_chunks(64))
        assert faces.dtype == result.position_ids.dtype == np.uint32
        for array in (result.vertices, faces, result.position_ids):
            assert not array.flags.writeable
            assert np.shares_memory(array, np.frombuffer(payload, dtype=np.uint8))
        np.testing.assert_array_equal(
            canonical(result).smooth_normals,
            canonical(deferred_geometry).smooth_normals,
        )
        assert result._canonical is None
        assert len(key) == 64

    def test_distinguishes_normal_representations(self, deferred_geometry):
        from app.modules.media.compute_geometry import (
            GeometryLayout,
            identity,
            layout_for,
            split,
        )

        layout = layout_for(deferred_geometry)
        canonical_layout = GeometryLayout(layout.counts)
        assert canonical_layout != layout
        body = split(encode(deferred_geometry, 64, 64, [None], False))[2]
        assert identity(layout, body) != identity(canonical_layout, body)

    @pytest.mark.parametrize("mode", [None, "canonical", "gpu-angle-csr-v0", 1])
    def test_rejects_unknown_explicit_normal_tags(self, deferred_geometry, mode):
        payload = encode(deferred_geometry, 64, 64, [None], False)
        with pytest.raises(ValueError, match="compute_geometry_normal_mode"):
            decode(rewrite_header(payload, {"normal_mode": mode}))

    def test_missing_tag_cannot_reinterpret_compact_arrays(self, deferred_geometry):
        payload = encode(deferred_geometry, 64, 64, [None], False)
        with pytest.raises(ValueError, match="compute_geometry_size"):
            decode(rewrite_header(payload, {"normal_mode": ...}))

    def test_retains_old_equivalent_admission_ceiling(self, deferred_geometry):
        from app.modules.media.compute_geometry import decode_header

        payload = rewrite_header(
            encode(deferred_geometry, 64, 64, [None], False),
            {"vertices": 1_200_000, "normals": 1_200_000},
        )
        size = struct.unpack("!I", payload[:4])[0]
        with pytest.raises(ValueError, match="compute_geometry_budget"):
            decode_header(payload[4 : 4 + size])

    @pytest.mark.parametrize(
        "kind", ["face", "identity", "position", "declared_positions"]
    )
    def test_rejects_invalid_compact_arrays(self, deferred_geometry, kind):
        payload = encode(deferred_geometry, 64, 64, [None], False)
        header_size = struct.unpack("!I", payload[:4])[0]
        start = 4 + header_size
        changed = bytearray(payload)
        if kind == "face":
            struct.pack_into("<I", changed, start + 36, 3)
        elif kind == "identity":
            struct.pack_into("<I", changed, start + 48, 3)
        elif kind == "position":
            struct.pack_into("<f", changed, start, float("nan"))
        else:
            struct.pack_into("<III", changed, start + 48, 0, 0, 0)
        with pytest.raises(ValueError, match="compute_geometry_invalid"):
            decode(bytes(changed))

    def test_rejects_mutable_backing_even_through_readonly_view(
        self, deferred_geometry
    ):
        from app.modules.media.compute_geometry import decode_arrays, layout_for, split

        body = split(encode(deferred_geometry, 64, 64, [None], False))[2]
        external = memoryview(bytearray(body)).toreadonly()
        with pytest.raises(ValueError, match="compute_geometry_mutable"):
            decode_arrays(external, layout_for(deferred_geometry))
