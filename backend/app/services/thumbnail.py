"""Extract base64-encoded PNG thumbnails embedded in OrcaSlicer/PrusaSlicer G-code."""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import List, Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)


def to_webp(data: bytes) -> bytes:
    """Re-encode image bytes (PNG from slicers/rasteriser) as lossless WebP.

    Single conversion seam for every thumbnail write. Lossless keeps the
    output pixel-identical to the source — no colour shift, no edge bleed on
    the transparent background — while still shrinking these flat-shaded
    renders below the original PNG. ``exact=True`` preserves the RGB of fully
    transparent pixels so the encoder can't recolour hidden areas.

    Returns the input unchanged when it is already WebP or when re-encoding
    fails — a stored thumbnail in the original format beats no thumbnail.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return data
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            buf = io.BytesIO()
            img.save(buf, format="WEBP", lossless=True, exact=True, method=6)
            return buf.getvalue()
    except Exception:
        logger.warning("thumbnail: webp conversion failed", exc_info=True)
        return data


_BEGIN_RE = re.compile(r";\s*thumbnail begin\s+(\d+)x(\d+)\s+(\d+)", re.IGNORECASE)
_END_RE = re.compile(r";\s*thumbnail end", re.IGNORECASE)

# Slicers embed thumbnails in the comment header before any print moves.
# Once this many non-comment (command) lines have streamed past outside a
# thumbnail block, stop scanning instead of walking the whole file — G-code
# bodies routinely run to hundreds of MB.
_MAX_COMMAND_LINES = 2048


def _iter_blocks(path: Path):
    """Yield (width, height, base64_string) for each embedded thumbnail block."""
    in_block = False
    width = height = 0
    buf: List[str] = []
    command_lines = 0

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not in_block:
                m = _BEGIN_RE.search(line)
                if m:
                    width = int(m.group(1))
                    height = int(m.group(2))
                    buf = []
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

            # Strip leading "; " (or ";") and any whitespace.
            stripped = line.lstrip()
            if stripped.startswith(";"):
                stripped = stripped[1:].strip()
            buf.append(stripped)


def extract(path: Path) -> Optional[bytes]:
    """Return PNG bytes of the largest embedded thumbnail, or None."""
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
        return base64.b64decode(best[1], validate=False)
    except (ValueError, base64.binascii.Error) as e:
        logger.warning("thumbnail extract: base64 decode failed: %s", e)
        return None
