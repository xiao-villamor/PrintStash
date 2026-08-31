"""Extract base64-encoded PNG thumbnails embedded in OrcaSlicer/PrusaSlicer G-code."""

from __future__ import annotations

import base64
import binascii
import io
import re
import warnings
from pathlib import Path
from typing import List, Optional, Tuple

from app.core.config import settings
from app.core.logging import get_logger
from app.services import bgcode

logger = get_logger(__name__)
_MAX_IMAGE_PIXELS = 25_000_000
_MAX_SCAN_BYTES = 16 * 1024 * 1024
_MAX_LINE_BYTES = 1024 * 1024
_MAX_BASE64_BYTES = 16 * 1024 * 1024


class ThumbnailValidationError(ValueError):
    """A decodable candidate failed the canonical thumbnail contract."""


def to_webp(data: bytes, *, normalize: bool = True) -> bytes:
    """Re-encode image bytes (PNG from slicers/rasteriser) as lossless WebP.

    Single conversion seam for every thumbnail write. Lossless keeps the
    output pixel-identical to the source — no colour shift, no edge bleed on
    the transparent background — while still shrinking these flat-shaded
    renders below the original PNG. ``exact=True`` preserves the RGB of fully
    transparent pixels so the encoder can't recolour hidden areas.

    Raises a stable error when validation or encoding fails. Callers treat the
    thumbnail as a retryable derivative; hostile input is never stored raw.
    """
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                if img.width * img.height > _MAX_IMAGE_PIXELS:
                    raise ValueError("thumbnail_too_large")
                img.load()
                width = int(settings.model_thumbnail_width)
                height = round(width * 3 / 4)
                source_had_alpha = "A" in img.getbands()
                source_is_webp = img.format == "WEBP"
                rgba = img.convert("RGBA")
                alpha_bounds = rgba.getchannel("A").getbbox()
                if alpha_bounds is None:
                    raise ThumbnailValidationError("thumbnail_empty")

                if normalize:
                    from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

                    canonical_with_safe_border = (
                        source_had_alpha
                        and rgba.size == (width, height)
                        and alpha_bounds[0] > 0
                        and alpha_bounds[1] > 0
                        and alpha_bounds[2] < width
                        and alpha_bounds[3] < height
                        and 0.76
                        <= max(
                            (alpha_bounds[2] - alpha_bounds[0]) / width,
                            (alpha_bounds[3] - alpha_bounds[1]) / height,
                        )
                        <= 0.84
                    )
                    if not canonical_with_safe_border:
                        rgba = rgba.crop(alpha_bounds)
                        margin = PREVIEW_PROFILE.margin_fraction
                        content_size = (
                            max(round(width * (1 - 2 * margin)), 1),
                            max(round(height * (1 - 2 * margin)), 1),
                        )
                        rgba.thumbnail(content_size, Image.Resampling.LANCZOS)
                        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                        offset = (
                            (width - rgba.width) // 2,
                            (height - rgba.height) // 2,
                        )
                        canvas.alpha_composite(rgba, dest=offset)
                        rgba = canvas
                    elif source_is_webp:
                        # The full renderer already encoded the canonical,
                        # lossless recipe. Validation above is still shared;
                        # only the redundant second WebP encode is skipped.
                        return data
                else:
                    rgba.thumbnail((width, height), Image.Resampling.LANCZOS)

                buf = io.BytesIO()
                rgba.save(buf, format="WEBP", lossless=True, exact=True, method=6)
                return buf.getvalue()
    except ThumbnailValidationError:
        raise
    except Exception as exc:
        logger.warning("thumbnail: webp conversion failed", exc_info=True)
        raise ValueError("thumbnail_too_large") from exc


_BEGIN_RE = re.compile(r";\s*thumbnail begin\s+(\d+)x(\d+)\s+(\d+)", re.IGNORECASE)
_END_RE = re.compile(r";\s*thumbnail end", re.IGNORECASE)

# Slicers embed thumbnails in the comment header before any print moves.
# Once this many non-comment (command) lines have streamed past outside a
# thumbnail block, stop scanning instead of walking the whole file — G-code
# bodies routinely run to hundreds of MB.
_MAX_COMMAND_LINES = 2048

# Cap on base64 lines accumulated within a single block. A real thumbnail (even
# a 640x480 plate preview) is well under this; the limit stops an unterminated
# "thumbnail begin" — a truncated or malicious file with no matching "end" —
# from buffering the entire G-code body into memory.
_MAX_BLOCK_LINES = 8192


def _iter_blocks(path: Path):
    """Yield (width, height, base64_string) for each embedded thumbnail block."""
    in_block = False
    width = height = 0
    buf: List[str] = []
    command_lines = 0

    scanned = 0
    block_bytes = 0
    with path.open("rb") as fh:
        while scanned < _MAX_SCAN_BYTES:
            raw_line = fh.readline(_MAX_LINE_BYTES + 1)
            if not raw_line:
                return
            scanned += len(raw_line)
            if len(raw_line) > _MAX_LINE_BYTES or not raw_line.endswith((b"\n", b"\r")):
                logger.warning("thumbnail: line exceeds byte limit in %s", path.name)
                return
            line = raw_line.decode("utf-8", errors="replace")
            if not in_block:
                m = _BEGIN_RE.search(line)
                if m:
                    width = int(m.group(1))
                    height = int(m.group(2))
                    declared = int(m.group(3))
                    if (
                        width * height > _MAX_IMAGE_PIXELS
                        or declared > _MAX_BASE64_BYTES
                    ):
                        return
                    buf = []
                    block_bytes = 0
                    in_block = True
                    continue
                stripped = line.lstrip()
                if stripped and not stripped.startswith(";"):
                    command_lines += 1
                    if command_lines >= _MAX_COMMAND_LINES:
                        return
                continue

            if _END_RE.search(line):
                yield width, height, "".join(buf)
                in_block = False
                buf = []
                continue

            if len(buf) >= _MAX_BLOCK_LINES:
                # No "thumbnail end" in sight — abandon this runaway block rather
                # than buffer the rest of the file, and resume normal scanning.
                logger.warning(
                    "thumbnail: abandoning unterminated thumbnail block in %s",
                    path.name,
                )
                in_block = False
                buf = []
                continue

            # Strip leading "; " (or ";") and any whitespace.
            stripped = line.lstrip()
            if stripped.startswith(";"):
                stripped = stripped[1:].strip()
            block_bytes += len(stripped)
            if block_bytes > _MAX_BASE64_BYTES:
                return
            buf.append(stripped)


def _extract_bgcode(path: Path) -> Optional[bytes]:
    """Return the largest PNG/JPG thumbnail embedded in a bgcode file, or None.

    Thumbnails are stored as raw image bytes (not base64) in dedicated blocks.
    QOI is skipped — Pillow can't decode it without a plugin, and bgcode always
    ships a PNG alongside it."""
    best: Optional[Tuple[int, bytes]] = None  # (area, image_bytes)
    for fmt, width, height, data in bgcode.iter_thumbnails(path):
        if bgcode.THUMBNAIL_FORMATS.get(fmt) not in ("png", "jpg"):
            continue
        area = width * height
        if best is None or area > best[0]:
            best = (area, data)
    return best[1] if best else None


def extract(path: Path) -> Optional[bytes]:
    """Return PNG bytes of the largest embedded thumbnail, or None."""
    if bgcode.is_bgcode(path):
        return _extract_bgcode(path)

    best: Optional[Tuple[int, str]] = None  # (area, b64)

    try:
        for w, h, b64 in _iter_blocks(path):
            area = w * h
            if best is None or area > best[0]:
                best = (area, b64)
    except OSError as e:
        logger.warning("thumbnail extract: cannot read %s: %s", path, e)
        return None

    if best is None:
        return None

    try:
        decoded = base64.b64decode(best[1], validate=True)
        return decoded if len(decoded) <= _MAX_BASE64_BYTES else None
    except (ValueError, binascii.Error) as e:
        logger.warning("thumbnail extract: base64 decode failed: %s", e)
        return None
