"""Compatibility facade for the framework-neutral binary G-code reader."""

from printstash_core.gcode import bgcode as _core

MAGIC = _core.MAGIC
THUMBNAIL_FORMATS = _core.THUMBNAIL_FORMATS
is_bgcode = _core.is_bgcode
is_valid_container = _core.is_valid_container
iter_thumbnails = _core.iter_thumbnails
read_metadata_text = _core.read_metadata_text

__all__ = [
    "MAGIC",
    "THUMBNAIL_FORMATS",
    "is_bgcode",
    "is_valid_container",
    "iter_thumbnails",
    "read_metadata_text",
]
