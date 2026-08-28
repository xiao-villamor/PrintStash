"""Unit tests for representative source-cover image normalization."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.core.config import _overlay
from app.services import source_cover_processing


def _image_bytes(
    image_format: str = "PNG", *, size: tuple[int, int] = (800, 600), **save_kwargs
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "navy").save(buffer, format=image_format, **save_kwargs)
    return buffer.getvalue()


class TestProcessSourceCoverUpload:
    @pytest.mark.parametrize(
        ("content_type", "image_format"),
        [
            ("image/jpeg", "JPEG"),
            ("image/png", "PNG"),
            ("image/webp", "WEBP"),
        ],
    )
    def test_normalizes_each_allowed_source_format_to_webp(
        self, content_type: str, image_format: str
    ) -> None:
        processed = source_cover_processing.process_source_cover_upload(
            _image_bytes(image_format), content_type
        )

        assert processed.content_type == "image/webp"
        with Image.open(io.BytesIO(processed.data)) as output:
            assert output.format == "WEBP"

    def test_uses_shared_thumbnail_conversion_seam(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _image_bytes()
        converted = b"shared-thumbnail-result"
        calls: list[bytes] = []

        def _to_webp(data: bytes) -> bytes:
            calls.append(data)
            return converted

        monkeypatch.setattr(source_cover_processing.thumbnail, "to_webp", _to_webp)

        processed = source_cover_processing.process_source_cover_upload(
            source, "image/png"
        )

        assert processed.data == converted
        assert calls == [source]

    def test_resizes_to_the_configured_thumbnail_width(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 320)

        processed = source_cover_processing.process_source_cover_upload(
            _image_bytes("PNG"), "image/png"
        )

        with Image.open(io.BytesIO(processed.data)) as output:
            assert output.size == (320, 240)

    def test_strips_every_metadata_block_from_the_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A cover comes from a third-party page, so its metadata is untrusted input
        # we would otherwise re-serve to every viewer.
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 320)
        source = _image_bytes("PNG", pnginfo=_png_info("do not retain"))

        processed = source_cover_processing.process_source_cover_upload(
            source, "image/png"
        )

        with Image.open(io.BytesIO(processed.data)) as output:
            assert "comment" not in output.info
            assert "exif" not in output.info
            assert "icc_profile" not in output.info

    def test_produces_deterministic_webp_bytes(self) -> None:
        source = _image_bytes("PNG")

        first = source_cover_processing.process_source_cover_upload(source, "image/png")
        second = source_cover_processing.process_source_cover_upload(
            source, "image/png"
        )

        assert first == second


def _png_info(comment: str):
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("comment", comment)
    return info


class TestValidateSourceCover:
    @pytest.mark.parametrize(
        ("content_type", "payload"),
        [
            ("image/gif", _image_bytes()),
            ("image/png", _image_bytes("JPEG")),
            ("image/png", b"not an image"),
            (None, _image_bytes()),
        ],
    )
    def test_rejects_invalid_content_type_or_image_before_conversion(
        self, monkeypatch: pytest.MonkeyPatch, content_type: str | None, payload: bytes
    ) -> None:
        monkeypatch.setattr(
            source_cover_processing.thumbnail,
            "to_webp",
            lambda _data: pytest.fail("invalid source cover reached conversion"),
        )

        with pytest.raises(source_cover_processing.SourceCoverProcessingError) as exc:
            source_cover_processing.process_source_cover_upload(payload, content_type)

        assert str(exc.value) == "source_cover_invalid"

    def test_rejects_payload_over_byte_limit_before_decoding(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            source_cover_processing,
            "MAX_SOURCE_COVER_BYTES",
            32,
        )
        monkeypatch.setattr(
            "PIL.Image.open",
            lambda _data: pytest.fail("oversized source was decoded"),
        )

        with pytest.raises(source_cover_processing.SourceCoverProcessingError) as exc:
            source_cover_processing.process_source_cover_upload(b"x" * 33, "image/png")

        assert str(exc.value) == "source_cover_invalid"

    def test_rejects_decoded_image_over_pixel_limit_before_conversion(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(source_cover_processing, "MAX_SOURCE_COVER_PIXELS", 100)
        monkeypatch.setattr(
            source_cover_processing.thumbnail,
            "to_webp",
            lambda _data: pytest.fail("oversized source cover reached conversion"),
        )

        with pytest.raises(source_cover_processing.SourceCoverProcessingError) as exc:
            source_cover_processing.process_source_cover_upload(
                _image_bytes(size=(11, 10)), "image/png"
            )

        assert str(exc.value) == "source_cover_invalid"

    def test_redacts_pillow_decompression_bomb_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)

        with pytest.raises(source_cover_processing.SourceCoverProcessingError) as exc:
            source_cover_processing.process_source_cover_upload(
                _image_bytes(size=(2, 1)), "image/png"
            )

        assert str(exc.value) == "source_cover_invalid"

    @pytest.mark.parametrize(
        "payload", [_image_bytes()[:-16], _image_bytes()[:8] + b"\xff" * 64]
    )
    def test_redacts_truncated_decode_failures(self, payload: bytes) -> None:
        with pytest.raises(source_cover_processing.SourceCoverProcessingError) as exc:
            source_cover_processing.process_source_cover_upload(payload, "image/png")

        assert str(exc.value) == "source_cover_invalid"
