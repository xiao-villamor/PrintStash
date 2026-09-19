"""Public compatibility facade for the native binary G-code reader."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .native import kernel

MAGIC = b"GCDE"
THUMBNAIL_FORMATS = {0: "png", 1: "jpg", 2: "qoi"}


def is_bgcode(path: Path) -> bool:
    """Return whether a file starts with the binary G-code magic."""
    return kernel().is_bgcode(path)


def is_valid_container(path: Path) -> bool:
    """Validate a BGCODE container without decoding its printable body."""
    return kernel().is_valid_bgcode(path)


def read_metadata_text(path: Path) -> str | None:
    """Render bounded BGCODE metadata blocks as G-code comments."""
    return kernel().bgcode_metadata_text(path)


def iter_thumbnails(path: Path) -> Iterator[tuple[int, int, int, bytes]]:
    """Yield ``(format, width, height, image_bytes)`` thumbnail records."""
    return iter(kernel().gcode_thumbnails(path))
