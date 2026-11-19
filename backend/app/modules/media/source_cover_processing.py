"""Validation and normalization for one user-supplied source cover image.

This module deliberately has no storage or database dependency.  Its caller
must process the complete upload before creating a storage object, so invalid
or hostile input can never leave an orphaned durable write.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from typing import Literal

from printstash_core.mesh.rasterizer import RenderedPixels, encode_rendered_pixels

MAX_SOURCE_COVER_BYTES = 15 * 1024 * 1024
MAX_SOURCE_COVER_PIXELS = 25_000_000

_CONTENT_TYPE_TO_FORMAT = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


class SourceCoverProcessingError(ValueError):
    """A deliberately redacted source-cover validation failure."""

    def __init__(self) -> None:
        super().__init__("source_cover_invalid")


@dataclass(frozen=True)
class ProcessedSourceCover:
    """The only bytes suitable for a source-cover storage write."""

    data: bytes
    content_type: Literal["image/webp"] = "image/webp"


def _normalized_content_type(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    return value.split(";", 1)[0].strip().lower()


def _validate_source_cover(data: bytes, content_type: str | None) -> None:
    normalized_content_type = _normalized_content_type(content_type)
    if normalized_content_type is None:
        raise SourceCoverProcessingError
    expected_format = _CONTENT_TYPE_TO_FORMAT.get(normalized_content_type)
    if expected_format is None or not data or len(data) > MAX_SOURCE_COVER_BYTES:
        raise SourceCoverProcessingError

    try:
        from PIL import Image

        # PIL emits its bomb signal while reading image headers. Turn it into a
        # normal validation failure rather than allowing a process-wide warning.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != expected_format:
                    raise SourceCoverProcessingError
                if image.width <= 0 or image.height <= 0:
                    raise SourceCoverProcessingError
                if image.width * image.height > MAX_SOURCE_COVER_PIXELS:
                    raise SourceCoverProcessingError
                image.verify()
    except SourceCoverProcessingError:
        raise
    except Exception as exc:
        raise SourceCoverProcessingError from exc


def process_source_cover_upload(
    data: bytes, content_type: str | None
) -> ProcessedSourceCover:
    """Retain the uploaded cover's resolution in a metadata-free WebP."""
    from PIL import Image, ImageOps

    _validate_source_cover(data, content_type)
    try:
        with Image.open(io.BytesIO(data)) as image:
            rgba = ImageOps.exif_transpose(image).convert("RGBA")
            if rgba.getchannel("A").getbbox() is None:
                raise SourceCoverProcessingError
            encoded = encode_rendered_pixels(
                RenderedPixels(rgba.width, rgba.height, rgba.tobytes()),
                output_format="WEBP",
            )
        if len(encoded) > MAX_SOURCE_COVER_BYTES:
            raise SourceCoverProcessingError
        return ProcessedSourceCover(data=encoded)
    except Exception as exc:
        # Do not surface decoder messages, source filenames, or binary details.
        raise SourceCoverProcessingError from exc
