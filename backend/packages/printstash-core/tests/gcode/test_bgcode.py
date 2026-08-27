"""Defends ``test_valid_container_and_typed_metadata`` behavior for the ``gcode`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from printstash_core.gcode import (
    GcodeMetadata,
    MaterialRequirement,
    bgcode,
    is_valid_container,
    parse,
)

_FILE_METADATA = 0
_GCODE = 1
_THUMBNAIL = 5


def _block(
    block_type: int,
    params: bytes,
    data: bytes,
    *,
    compression: int,
) -> bytes:
    body = zlib.compress(data) if compression == 1 else data
    header = struct.pack("<HHI", block_type, compression, len(data))
    if compression != 0:
        header += struct.pack("<I", len(body))
    return header + params + body


def _container(*blocks: bytes) -> bytes:
    return b"GCDE" + struct.pack("<IH", 1, 0) + b"".join(blocks)


def _sample_container() -> bytes:
    metadata = _block(
        _FILE_METADATA,
        struct.pack("<H", 0),
        b"Producer=PrusaSlicer 2.8.0\nfilament_type=PETG\n",
        compression=1,
    )
    thumbnail = _block(
        _THUMBNAIL,
        struct.pack("<HHH", 0, 16, 16),
        b"thumbnail-bytes",
        compression=1,
    )
    gcode = _block(
        _GCODE,
        struct.pack("<H", 2),
        b"opaque-heatshrink",
        compression=3,
    )
    return _container(metadata, thumbnail, gcode)


def test_valid_container_and_typed_metadata(tmp_path: Path) -> None:
    path = tmp_path / "sample.bgcode"
    path.write_bytes(_sample_container())

    assert is_valid_container(path) is True
    assert parse(path) == GcodeMetadata(
        slicer_name="PrusaSlicer",
        slicer_version="PrusaSlicer 2.8.0",
        material_type="PETG",
        material_requirements=(MaterialRequirement(0, "PETG"),),
    )
    assert list(bgcode.iter_thumbnails(path)) == [(0, 16, 16, b"thumbnail-bytes")]


def test_container_validation_rejects_truncation_and_bad_deflate(
    tmp_path: Path,
) -> None:
    truncated = tmp_path / "truncated.bgcode"
    truncated.write_bytes(_sample_container()[:40])
    assert is_valid_container(truncated) is False

    corrupt_metadata = (
        struct.pack("<HHI", _FILE_METADATA, 1, 1000)
        + struct.pack("<I", len(b"not-deflate"))
        + struct.pack("<H", 0)
        + b"not-deflate"
    )
    gcode = _block(
        _GCODE,
        struct.pack("<H", 2),
        b"opaque",
        compression=3,
    )
    corrupt = tmp_path / "corrupt.bgcode"
    corrupt.write_bytes(_container(corrupt_metadata, gcode))
    assert is_valid_container(corrupt) is False


def test_bounded_decompress_rejects_oversize_partial_and_trailing_streams() -> None:
    compressed = zlib.compress(b"x" * 8192)
    assert bgcode._decompress(1, compressed, max_output=1024) is None

    valid = zlib.compress(b"metadata")
    assert bgcode._decompress(1, valid[:-1], max_output=1024) is None
    assert bgcode._decompress(1, valid + b"trailing", max_output=1024) is None


def test_container_requires_a_printable_gcode_block(tmp_path: Path) -> None:
    metadata_only = tmp_path / "metadata-only.bgcode"
    metadata_only.write_bytes(
        _container(
            _block(
                _FILE_METADATA,
                struct.pack("<H", 0),
                b"Producer=PrusaSlicer 2.8.0\n",
                compression=0,
            )
        )
    )

    assert is_valid_container(metadata_only) is False


def test_container_validation_skips_large_printable_body(tmp_path: Path) -> None:
    """Printable blocks are seeked, not read, so their size is not metadata-capped."""
    path = tmp_path / "large-sparse.bgcode"
    body_size = bgcode._MAX_BLOCK_DATA + 1
    with path.open("wb") as file:
        file.write(b"GCDE" + struct.pack("<IH", 1, 0))
        file.write(struct.pack("<HHI", _GCODE, 0, body_size))
        file.write(struct.pack("<H", 2))
        file.seek(body_size - 1, 1)
        file.write(b"\0")

    assert is_valid_container(path) is True
