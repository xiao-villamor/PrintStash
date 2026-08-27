"""Source-cover uploads become bounded, metadata-free WebP or fail redacted.

These tests defend the service boundary before cover bytes can be published.
"""

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


@pytest.mark.parametrize(
    ("content_type", "image_format"),
    [
        pytest.param("image/jpeg", "JPEG", id="jpeg"),
        pytest.param("image/png", "PNG", id="png"),
        pytest.param("image/webp", "WEBP", id="webp"),
    ],
)
def test_normalizes_each_allowed_source_format_to_webp(
    content_type: str, image_format: str
) -> None:
    processed = source_cover_processing.process_source_cover_upload(
        _image_bytes(image_format), content_type
    )

    assert processed.content_type == "image/webp"
    with Image.open(io.BytesIO(processed.data)) as output:
        assert output.format == "WEBP"


def test_accepts_a_normalized_media_type_with_parameters() -> None:
    processed = source_cover_processing.process_source_cover_upload(
        _image_bytes(), "image/png; charset=binary"
    )

    with Image.open(io.BytesIO(processed.data)) as output:
        assert output.format == "WEBP"


def test_uses_shared_thumbnail_conversion_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _image_bytes()
    converted = b"shared-thumbnail-result"
    calls: list[bytes] = []

    def _to_webp(data: bytes) -> bytes:
        calls.append(data)
        return converted

    monkeypatch.setattr(source_cover_processing.thumbnail, "to_webp", _to_webp)

    processed = source_cover_processing.process_source_cover_upload(source, "image/png")

    assert processed.data == converted
    assert calls == [source]


def test_strips_source_metadata_and_honours_configured_thumbnail_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_overlay, "model_thumbnail_width", 320)
    source = _image_bytes("PNG", pnginfo=_png_info("do not retain"))

    processed = source_cover_processing.process_source_cover_upload(source, "image/png")

    with Image.open(io.BytesIO(processed.data)) as output:
        assert output.size == (320, 240)
        assert "comment" not in output.info
        assert "exif" not in output.info
        assert "icc_profile" not in output.info


def test_produces_deterministic_webp_bytes() -> None:
    source = _image_bytes("PNG")

    first = source_cover_processing.process_source_cover_upload(source, "image/png")
    second = source_cover_processing.process_source_cover_upload(source, "image/png")

    assert first == second


def _png_info(comment: str):
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("comment", comment)
    return info


@pytest.mark.parametrize(
    "content_type",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="blank"),
        pytest.param("image/gif", id="gif"),
        pytest.param("image/svg+xml", id="svg"),
        pytest.param("application/octet-stream", id="binary"),
    ],
)
def test_rejects_missing_or_unsupported_content_type_before_conversion(
    monkeypatch: pytest.MonkeyPatch, content_type: str | None
) -> None:
    monkeypatch.setattr(
        source_cover_processing.thumbnail,
        "to_webp",
        lambda _data: pytest.fail("invalid source cover reached conversion"),
    )

    with pytest.raises(source_cover_processing.SourceCoverProcessingError) as exc:
        source_cover_processing.process_source_cover_upload(
            _image_bytes(), content_type
        )

    assert str(exc.value) == "source_cover_invalid"


@pytest.mark.parametrize(
    ("content_type", "payload"),
    [
        pytest.param("image/png", _image_bytes("JPEG"), id="format-mismatch"),
        pytest.param("image/jpeg", b"not an image", id="invalid-image"),
    ],
)
def test_rejects_mismatched_or_invalid_image_before_conversion(
    monkeypatch: pytest.MonkeyPatch, content_type: str, payload: bytes
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


def test_accepts_a_valid_source_cover_exactly_at_the_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _image_bytes()
    monkeypatch.setattr(source_cover_processing, "MAX_SOURCE_COVER_BYTES", len(source))

    processed = source_cover_processing.process_source_cover_upload(source, "image/png")

    with Image.open(io.BytesIO(processed.data)) as output:
        assert output.format == "WEBP"


def test_rejects_decoded_image_over_pixel_limit_before_conversion(
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)

    with pytest.raises(source_cover_processing.SourceCoverProcessingError) as exc:
        source_cover_processing.process_source_cover_upload(
            _image_bytes(size=(2, 1)), "image/png"
        )

    assert str(exc.value) == "source_cover_invalid"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_image_bytes()[:-16], id="truncated-png"),
        pytest.param(_image_bytes()[:8] + b"\xff" * 64, id="corrupt-png"),
    ],
)
def test_redacts_truncated_decode_failures(payload: bytes) -> None:
    with pytest.raises(source_cover_processing.SourceCoverProcessingError) as exc:
        source_cover_processing.process_source_cover_upload(payload, "image/png")

    assert str(exc.value) == "source_cover_invalid"
