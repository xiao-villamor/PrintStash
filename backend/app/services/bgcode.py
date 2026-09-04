"""Compatibility facade for the framework-neutral binary G-code reader."""

from printstash_core.gcode import bgcode as _core

MAGIC = _core.MAGIC
THUMBNAIL_FORMATS = _core.THUMBNAIL_FORMATS
is_bgcode = _core.is_bgcode
is_valid_container = _core.is_valid_container
iter_thumbnails = _core.iter_thumbnails
read_metadata_text = _core.read_metadata_text

# Preserve low-level test/debug imports during the core-package extraction.
_MAX_BLOCKS = _core._MAX_BLOCKS
_MAX_BLOCK_DATA = _core._MAX_BLOCK_DATA
_MAX_METADATA_BYTES = _core._MAX_METADATA_BYTES
_MAX_THUMBNAIL_BYTES = _core._MAX_THUMBNAIL_BYTES
_block_param_len = _core._block_param_len
_decompress = _core._decompress
_parse_file_header = _core._parse_file_header
_walk = _core._walk

__all__ = [
    "MAGIC",
    "THUMBNAIL_FORMATS",
    "is_bgcode",
    "is_valid_container",
    "iter_thumbnails",
    "read_metadata_text",
]
