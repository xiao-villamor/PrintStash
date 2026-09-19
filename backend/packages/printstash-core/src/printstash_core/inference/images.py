"""Decode untrusted query images into bounded, metadata-free RGB inputs."""

from __future__ import annotations

import io
import warnings
from typing import cast

from .embedding import EmbeddingError, EmbeddingInput

MAX_IMAGE_BYTES = 8 * 1024**2
MAX_IMAGE_EDGE = 8192
MAX_IMAGE_PIXELS = 16_777_216
_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def decode_image(payload: bytes, content_type: str) -> EmbeddingInput:
    """Accept one static PNG/JPEG/WebP, apply orientation and discard metadata.

    The encoded body and source dimensions are checked before pixel decoding.
    Only RGB pixels cross the inference boundary; no filename, EXIF, text chunks
    or original body is retained. Pillow remains an optional image capability.
    """
    if len(payload) > MAX_IMAGE_BYTES:
        raise EmbeddingError("embedding_image_too_large")
    mime = content_type.partition(";")[0].strip().lower()
    if mime not in _FORMATS.values():
        raise EmbeddingError("embedding_image_type_unsupported")
    try:
        from PIL import (  # pyright: ignore[reportMissingTypeStubs]
            Image,
            ImageOps,
            UnidentifiedImageError,
        )
    except ImportError:
        raise EmbeddingError("embedding_runtime_unavailable") from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as source:
                actual = _FORMATS.get(source.format or "")
                if actual is None:
                    raise EmbeddingError("embedding_image_type_unsupported")
                if actual != mime:
                    raise EmbeddingError("embedding_image_type_mismatch")
                width, height = source.size
                if (
                    min(width, height) < 1
                    or max(width, height) > MAX_IMAGE_EDGE
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise EmbeddingError("embedding_image_too_large")
                if getattr(source, "n_frames", 1) != 1:
                    raise EmbeddingError("embedding_image_animation_unsupported")
                source.load()
                # Pillow 10 annotates the optional in-place return even though
                # the default used here always returns an image.
                oriented = cast(Image.Image, ImageOps.exif_transpose(source))  # pyright: ignore[reportUnnecessaryCast]
                oriented.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                rgba = oriented.convert("RGBA")
                background = Image.new("RGBA", rgba.size, 0xFFFFFFFF)
                background.alpha_composite(rgba)
                rgb = background.convert("RGB")
                return EmbeddingInput(
                    "image", rgb=rgb.tobytes(), width=rgb.width, height=rgb.height
                )
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise EmbeddingError("embedding_image_too_large") from None
    except EmbeddingError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, EOFError):
        raise EmbeddingError("embedding_image_invalid") from None
    except (ValueError, TypeError, OverflowError):
        raise EmbeddingError("embedding_image_invalid") from None
