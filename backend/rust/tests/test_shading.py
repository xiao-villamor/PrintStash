"""The native shader validates its immutable boundary and preserves light output."""

import numpy as np
import printstash_mesh_native as native
import pytest


@pytest.fixture
def shade_args():
    triangle = np.array([[[0, 0, 1], [4, 0, 1], [0, 4, 1]]], dtype=np.float64)
    normal = np.array([[[0, 0, 1]] * 3], dtype=np.float64)
    records, _ = native.rasterize(
        triangle.tobytes(), 8, np.full((4, 4), np.inf).tobytes(), 4, 4
    )
    # Black directional lights, half-intensity neutral ambient, white output scale.
    lighting = [0.0] * 31
    lighting[9:12] = [1.0] * 3
    lighting[24] = 0.5
    lighting[26:28] = [3.0, 32.0]
    lighting[28:31] = [255.0] * 3
    return dict(
        records=records,
        triangles=triangle.tobytes(),
        itemsize=8,
        normals=normal.tobytes(),
        normal_itemsize=8,
        width=4,
        height=4,
        lighting=lighting,
    )


class TestShadeFragments:
    def test_paints_constant_ambient(self, shade_args):
        colors = native.shade_fragments(**shade_args)
        assert colors == bytes([127] * 30)

    def test_accepts_empty_fragments(self, shade_args):
        shade_args["records"] = b""
        assert native.shade_fragments(**shade_args) == b""

    @pytest.mark.parametrize(
        ("key", "value", "match"),
        [
            ("records", b"x", "fragment buffer"),
            ("records", bytes(24 * 17), "fragment buffer"),
            ("triangles", b"x", "triangle buffer"),
            ("normals", b"x", "normal buffer"),
            ("itemsize", 2, "float width"),
            ("normal_itemsize", 2, "float width"),
            ("width", 0, "dimensions"),
            ("width", 2**32, "dimensions"),
        ],
    )
    def test_rejects_malformed_buffers(self, shade_args, key, value, match):
        shade_args[key] = value
        with pytest.raises(ValueError, match=match):
            native.shade_fragments(**shade_args)

    @pytest.mark.parametrize("field", [0, 1])
    def test_rejects_out_of_bounds_fragment(self, shade_args, field):
        records = (
            np.frombuffer(shade_args["records"], dtype=np.uint64).copy().reshape(-1, 3)
        )
        records[0, field] = 2**64 - 1
        shade_args["records"] = records.tobytes()
        with pytest.raises(ValueError, match="fragment index"):
            native.shade_fragments(**shade_args)

    @pytest.mark.parametrize("field", ["triangles", "normals"])
    def test_rejects_nonfinite_mesh_values(self, shade_args, field):
        values = np.frombuffer(shade_args[field], dtype=np.float64).copy()
        values[-1] = np.nan
        shade_args[field] = values.tobytes()
        with pytest.raises(ValueError, match="finite"):
            native.shade_fragments(**shade_args)

    @pytest.mark.parametrize(("index", "value"), [(0, np.nan), (26, -1), (27, np.inf)])
    def test_rejects_invalid_lighting(self, shade_args, index, value):
        shade_args["lighting"][index] = value
        with pytest.raises(ValueError, match="lighting"):
            native.shade_fragments(**shade_args)

    def test_rejects_degenerate_fragment(self, shade_args):
        shade_args["triangles"] = np.zeros(9).tobytes()
        with pytest.raises(ValueError, match="degenerate"):
            native.shade_fragments(**shade_args)

    def test_preserves_zero_normals(self, shade_args):
        shade_args["normals"] = np.zeros(9).tobytes()
        assert native.shade_fragments(**shade_args) == bytes([127] * 30)

    def test_rejects_overflowing_interpolation(self, shade_args):
        shade_args["normals"] = np.full(9, 1e308).tobytes()
        with pytest.raises(ValueError, match="interpolated normals must be finite"):
            native.shade_fragments(**shade_args)

    def test_rejects_mutable_fragment_storage(self, shade_args):
        shade_args["records"] = bytearray(shade_args["records"])
        with pytest.raises(TypeError, match="bytes"):
            native.shade_fragments(**shade_args)


class TestRasterizePhong:
    def test_matches_separate_native_calls(self, shade_args):
        expected_colors = native.shade_fragments(**shade_args)
        expected = np.frombuffer(shade_args.pop("records"), dtype=np.uint64).reshape(
            -1, 3
        )
        shade_args["depth"] = np.full((4, 4), np.inf).tobytes()
        payload, candidates = native.rasterize_phong(**shade_args)
        records = np.frombuffer(
            payload, dtype=[("pixel", "=u4"), ("depth", "=f8"), ("rgb", "u1", (3,))]
        )
        np.testing.assert_array_equal(records["pixel"], expected[:, 0])
        np.testing.assert_array_equal(records["depth"], expected[:, 2].view(np.float64))
        assert records["rgb"].tobytes() == expected_colors
        assert candidates == 16

    def test_omits_occluded_fragments(self, shade_args):
        shade_args.pop("records")
        shade_args["depth"] = np.zeros((4, 4)).tobytes()
        payload, _ = native.rasterize_phong(**shade_args)
        assert payload == b""

    def test_rejects_short_depth(self, shade_args):
        shade_args.pop("records")
        shade_args["depth"] = b""
        with pytest.raises(ValueError, match="depth buffer"):
            native.rasterize_phong(**shade_args)

    def test_discards_failed_shading(self, shade_args):
        shade_args.pop("records")
        shade_args["depth"] = np.full((4, 4), np.inf).tobytes()
        shade_args["normals"] = np.full(9, 1e308).tobytes()
        with pytest.raises(ValueError, match="interpolated normals"):
            native.rasterize_phong(**shade_args)
