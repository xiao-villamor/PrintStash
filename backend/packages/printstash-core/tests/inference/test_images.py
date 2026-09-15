"""User image bytes become bounded, oriented RGB with no attached metadata."""

import io
import struct
import zlib

import pytest
from PIL import Image

from printstash_core.inference import EmbeddingError
from printstash_core.inference.images import decode_image


def png_header(width, height):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", b"")
    )


def encoded_image(format="PNG", size=(20, 10), **options):
    output = io.BytesIO()
    image = Image.new("RGB", size, (255, 0, 0))
    image.save(output, format, **options)
    return output.getvalue()


class TestDecodeImage:
    @pytest.mark.parametrize("failure", [ValueError, TypeError, OverflowError])
    def test_contains_metadata_decoder_failures(self, monkeypatch, failure):
        from PIL import ImageOps

        payload = encoded_image("JPEG")

        def invalid_metadata(*args, **kwargs):
            raise failure("invalid metadata")

        monkeypatch.setattr(ImageOps, "exif_transpose", invalid_metadata)
        with pytest.raises(EmbeddingError, match="^embedding_image_invalid$"):
            decode_image(payload, "image/jpeg")

    def test_returns_oriented_rgb_without_metadata(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = "private-location-marker"
        result = decode_image(encoded_image("JPEG", exif=exif), "image/jpeg")
        assert (result.width, result.height) == (10, 20)
        assert len(result.rgb) == 10 * 20 * 3
        assert b"private-location-marker" not in result.rgb
        assert result.text is None
        assert result.rgb[:3] == bytes((254, 0, 0))

    def test_downsizes_without_distorting_aspect_ratio(self):
        result = decode_image(encoded_image(size=(2048, 1024)), "image/png")
        assert (result.width, result.height) == (1024, 512)
        assert len(result.rgb) == 1024 * 512 * 3

    def test_composites_transparency_over_white(self):
        output = io.BytesIO()
        Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(output, "PNG")
        assert decode_image(output.getvalue(), "image/png").rgb == b"\xff" * 12

    @pytest.mark.parametrize(
        "payload,mime,code",
        [
            (b"not an image", "image/png", "embedding_image_invalid"),
            (b"", "image/png", "embedding_image_invalid"),
            (encoded_image(), "image/jpeg", "embedding_image_type_mismatch"),
            (encoded_image("GIF"), "image/gif", "embedding_image_type_unsupported"),
            (b"x" * (8 * 1024**2 + 1), "image/png", "embedding_image_too_large"),
            (png_header(8193, 2), "image/png", "embedding_image_too_large"),
            (png_header(8192, 8192), "image/png", "embedding_image_too_large"),
        ],
    )
    def test_rejects_invalid_or_oversized_images_before_decode(
        self, payload, mime, code
    ):
        with pytest.raises(EmbeddingError, match=f"^{code}$"):
            decode_image(payload, mime)

    def test_rejects_animated_images(self):
        output = io.BytesIO()
        first = Image.new("RGB", (5, 5), "red")
        first.save(
            output,
            "PNG",
            save_all=True,
            append_images=[Image.new("RGB", (5, 5), "blue")],
            duration=100,
        )
        with pytest.raises(
            EmbeddingError, match="embedding_image_animation_unsupported"
        ):
            decode_image(output.getvalue(), "image/png")

    def test_rejects_pillow_decompression_bombs(self, monkeypatch):
        payload = encoded_image()
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
        with pytest.raises(EmbeddingError, match="embedding_image_too_large"):
            decode_image(payload, "image/png")

    def test_reports_an_absent_image_runtime(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "PIL", None)
        with pytest.raises(EmbeddingError, match="embedding_runtime_unavailable"):
            decode_image(b"image", "image/png")

    def test_rejects_an_unsupported_decoded_format(self):
        with pytest.raises(EmbeddingError, match="embedding_image_type_unsupported"):
            decode_image(encoded_image("GIF"), "image/png")
