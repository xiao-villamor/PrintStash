"""Native image processing preserves pixels, transparency and bounded inputs."""

import io

import numpy as np
import printstash_mesh_native as native
import pytest
from PIL import Image


class TestProcessImage:
    @pytest.mark.parametrize("format", ["PNG", "WEBP"])
    def test_lossless_encoding_preserves_rgba(self, format):
        pixels = np.arange(8 * 9 * 4, dtype=np.uint8).reshape(8, 9, 4)

        encoded = native.process_image(
            pixels.tobytes(), 9, 8, 9, 8, "lanczos", True, False, format
        )
        decoded = np.asarray(Image.open(io.BytesIO(encoded)).convert("RGBA"))

        np.testing.assert_array_equal(decoded, pixels)

    @pytest.mark.parametrize(
        "filter,resample",
        [
            ("lanczos", Image.Resampling.LANCZOS),
            ("bilinear", Image.Resampling.BILINEAR),
        ],
    )
    def test_resize_preserves_visible_pixels(self, filter, resample):
        pixels = np.random.default_rng(7).integers(0, 256, (80, 96, 4), dtype=np.uint8)
        expected = np.asarray(
            Image.fromarray(pixels).resize((32, 27), resample)
        ).astype(float)

        encoded = native.process_image(
            pixels.tobytes(), 96, 80, 32, 27, filter, True, False, "PNG"
        )
        actual = np.asarray(Image.open(io.BytesIO(encoded))).astype(float)
        delta = np.abs(
            actual[:, :, :3] * actual[:, :, 3:] / 255
            - expected[:, :, :3] * expected[:, :, 3:] / 255
        )

        assert actual.shape == expected.shape
        assert delta.mean() < 1.0
        assert delta.max() < 6.0
        assert np.abs(actual[:, :, 3] - expected[:, :, 3]).max() <= 2

    @pytest.mark.parametrize("format", ["PNG", "WEBP"])
    def test_mesh_preview_uses_native_image_pipeline(self, format):
        import reference_native_adapter as native_rasterizer
        import reference_rasterizer as rasterizer
        import trimesh

        mesh = trimesh.creation.icosphere(subdivisions=2)
        options = dict(
            width=80,
            height=60,
            mesh_preparer=native_rasterizer.prepare_mesh,
            output_format=format,
        )
        expected_bytes = rasterizer.render_mesh_thumbnail(mesh, "sphere", **options)
        expected = np.asarray(
            Image.open(io.BytesIO(expected_bytes)).convert("RGBA")
        ).astype(float)

        encoded = rasterizer.render_mesh_thumbnail(
            mesh, "sphere", image_encoder=native_rasterizer.encode_preview, **options
        )
        actual = np.asarray(Image.open(io.BytesIO(encoded)).convert("RGBA")).astype(
            float
        )
        delta = np.abs(
            actual[:, :, :3] * actual[:, :, 3:] / 255
            - expected[:, :, :3] * expected[:, :, 3:] / 255
        )

        assert delta.mean() < 1.0
        assert delta.max() < 6.0
        assert np.abs(actual[:, :, 3] - expected[:, :, 3]).max() <= 2

    def test_transparent_holes_remain_transparent(self):
        pixels = np.full((64, 64, 4), 255, dtype=np.uint8)
        pixels[16:48, 16:48] = 0

        encoded = native.process_image(
            pixels.tobytes(), 64, 64, 32, 32, "lanczos", True, False, "PNG"
        )
        decoded = np.asarray(Image.open(io.BytesIO(encoded)))

        assert decoded[16, 16, 3] == 0
        assert decoded[2, 2, 3] == 255

    def test_vignette_preserves_alpha(self):
        pixels = bytes([100, 100, 100, 123]) * 9

        encoded = native.process_image(pixels, 3, 3, 3, 3, "lanczos", True, True, "PNG")
        decoded = np.asarray(Image.open(io.BytesIO(encoded)))

        assert decoded[0, 0].tolist() == [82, 82, 82, 123]
        assert decoded[1, 1].tolist() == [100, 100, 100, 123]

    @pytest.mark.parametrize(
        "args",
        [
            (b"", 1, 1, 1, 1, "lanczos", True, False, "PNG"),
            (b"", 0, 1, 1, 1, "lanczos", True, False, "PNG"),
            (b"", 4097, 1, 1, 1, "lanczos", True, False, "PNG"),
            (bytes(4), 1, 1, 4097, 1, "lanczos", True, False, "PNG"),
            (bytes(4), 1, 1, 1, 1, "bad", True, False, "PNG"),
            (bytes(4), 1, 1, 1, 1, "lanczos", True, False, "JPEG"),
        ],
    )
    def test_invalid_image_is_rejected(self, args):
        with pytest.raises(ValueError, match="invalid"):
            native.process_image(*args)


class TestShadeDepth:
    def test_flat_surface_preserves_hole(self):
        depth = np.zeros((5, 5), dtype=np.float32)
        depth[2, 2] = np.inf

        rgba = np.frombuffer(
            native.shade_depth(depth.tobytes(), 5, 5, 1.0, (0.5, 0.6, 0.7)),
            dtype=np.uint8,
        ).reshape(5, 5, 4)

        assert rgba[2, 2].tolist() == [0, 0, 0, 0]
        assert rgba[0, 0].tolist() == rgba[3, 3].tolist()
        assert rgba[0, 0, 3] == 255

    @pytest.mark.parametrize("scale", [0.0, -1.0, float("nan"), float("inf")])
    def test_invalid_depth_scale_is_rejected(self, scale):
        with pytest.raises(ValueError, match="invalid depth"):
            native.shade_depth(bytes(4), 1, 1, scale, (0.5, 0.5, 0.5))

    def test_invalid_depth_buffer_is_rejected(self):
        with pytest.raises(ValueError, match="invalid depth"):
            native.shade_depth(bytes(3), 1, 1, 1.0, (0.5, 0.5, 0.5))

    @pytest.mark.parametrize("scale", [0.01, 1.0, 100.0])
    def test_depth_shading_matches_reference(self, monkeypatch, scale):
        import reference_stl as worker
        from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

        depth = np.random.default_rng(18).normal(size=(31, 37)).astype(np.float32)
        depth[10:20, 12:24] = np.inf
        reference = np.asarray(
            Image.open(io.BytesIO(worker._encode_depth(depth, scale, 37, 31)))
        ).astype(int)

        output = native.shade_depth(
            depth.tobytes(), 37, 31, scale, PREVIEW_PROFILE.material_albedo
        )
        actual = np.frombuffer(output, dtype=np.uint8).reshape(31, 37, 4).astype(int)

        np.testing.assert_array_equal(actual[:, :, 3], reference[:, :, 3])
        assert np.abs(actual[:, :, :3] - reference[:, :, :3]).max() <= 1


class TestNormalizeThumbnail:
    @pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
    def test_normalizes_supported_image(self, format):
        source = io.BytesIO()
        Image.new("RGB", (40, 80), "white").save(source, format=format)

        encoded = native.normalize_thumbnail(source.getvalue(), 320, True, 0.10)
        image = Image.open(io.BytesIO(encoded)).convert("RGBA")

        assert image.size == (320, 240)
        assert image.getchannel("A").getbbox() == (112, 24, 208, 216)

    def test_declines_other_legacy_formats(self):
        source = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(source, format="BMP")

        assert native.normalize_thumbnail(source.getvalue(), 320, True, 0.1) is None

    def test_rejects_empty_image(self):
        source = io.BytesIO()
        Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(source, format="PNG")

        with pytest.raises(ValueError, match="thumbnail_empty"):
            native.normalize_thumbnail(source.getvalue(), 320, True, 0.1)

    def test_rejects_oversized_dimensions_before_decode(self):
        import struct
        import zlib

        source = io.BytesIO()
        Image.new("RGBA", (1, 1)).save(source, format="PNG")
        data = bytearray(source.getvalue())
        data[16:24] = struct.pack(">II", 25_000_001, 1)
        data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))

        with pytest.raises(ValueError, match="thumbnail_too_large"):
            native.normalize_thumbnail(bytes(data), 320, True, 0.1)

    @pytest.mark.parametrize(
        "width,margin",
        [(319, 0.1), (1281, 0.1), (640, -0.1), (640, 0.5), (640, float("nan"))],
    )
    def test_rejects_invalid_recipe(self, width, margin):
        with pytest.raises(ValueError, match="thumbnail_width_invalid"):
            native.normalize_thumbnail(b"", width, True, margin)

    def test_rejects_truncated_supported_image(self):
        with pytest.raises(ValueError):
            native.normalize_thumbnail(b"\x89PNG\r\n\x1a\n", 320, True, 0.1)
