"""Bounded reader for PrusaSlicer binary G-code containers."""

from __future__ import annotations

import logging
import struct
import zlib
from pathlib import Path
from typing import BinaryIO, Iterator, TypeAlias

logger = logging.getLogger(__name__)

MAGIC = b"GCDE"

_FILE_METADATA = 0
_GCODE = 1
_SLICER_METADATA = 2
_PRINTER_METADATA = 3
_PRINT_METADATA = 4
_THUMBNAIL = 5
_METADATA_TYPES = frozenset(
    {_FILE_METADATA, _SLICER_METADATA, _PRINTER_METADATA, _PRINT_METADATA}
)

_COMP_NONE = 0
_COMP_DEFLATE = 1

THUMBNAIL_FORMATS = {0: "png", 1: "jpg", 2: "qoi"}

_MAX_BLOCKS = 4096
_MAX_BLOCK_DATA = 64 * 1024 * 1024
_MAX_METADATA_BYTES = 8 * 1024 * 1024
_MAX_THUMBNAIL_BYTES = 32 * 1024 * 1024

_Block: TypeAlias = tuple[int, int, bytes, bytes]


def is_bgcode(path: Path) -> bool:
    """Return whether a file starts with the binary G-code magic."""
    try:
        with path.open("rb") as file:
            return file.read(4) == MAGIC
    except OSError as error:
        logger.warning("bgcode: cannot read %s: %s", path, error)
        return False


def is_valid_container(path: Path) -> bool:
    """Validate container framing without decoding the printable G-code body."""
    try:
        file_size = path.stat().st_size
        with path.open("rb") as file:
            checksum_type = _parse_file_header(file)
            if checksum_type not in {0, 1}:
                return False

            saw_block = False
            saw_gcode = False
            for _ in range(_MAX_BLOCKS):
                block_header = file.read(8)
                if not block_header:
                    return saw_block and saw_gcode
                if len(block_header) != 8:
                    return False

                saw_block = True
                block_type, compression, uncompressed_size = struct.unpack(
                    "<HHI", block_header
                )
                checksum = zlib.crc32(block_header)
                if compression == _COMP_NONE:
                    data_len = uncompressed_size
                else:
                    compressed_size_raw = file.read(4)
                    if len(compressed_size_raw) != 4:
                        return False
                    checksum = zlib.crc32(compressed_size_raw, checksum)
                    (data_len,) = struct.unpack("<I", compressed_size_raw)

                param_len = _block_param_len(block_type)
                params = file.read(param_len)
                if len(params) != param_len:
                    return False
                checksum = zlib.crc32(params, checksum)
                checksum_len = 4 if checksum_type != 0 else 0
                if file.tell() + data_len + checksum_len > file_size:
                    return False

                if block_type == _GCODE:
                    saw_gcode = True

                output_limit = _output_limit(block_type)
                if output_limit is not None:
                    if data_len > _MAX_BLOCK_DATA or uncompressed_size > output_limit:
                        return False
                    data = file.read(data_len)
                    if len(data) != data_len:
                        return False
                    checksum = zlib.crc32(data, checksum)
                    if compression not in {_COMP_NONE, _COMP_DEFLATE}:
                        return False
                    decoded = _decompress(
                        compression,
                        data,
                        max_output=output_limit,
                    )
                    if decoded is None or len(decoded) != uncompressed_size:
                        return False
                else:
                    if checksum_len:
                        consumed_checksum = _consume_crc(file, data_len, checksum)
                        if consumed_checksum is None:
                            return False
                        checksum = consumed_checksum
                    else:
                        file.seek(data_len, 1)

                if checksum_len:
                    checksum_raw = file.read(checksum_len)
                    if len(checksum_raw) != checksum_len:
                        return False
                    (expected_checksum,) = struct.unpack("<I", checksum_raw)
                    if expected_checksum != (checksum & 0xFFFFFFFF):
                        return False
            return False
    except (OSError, struct.error, ValueError):
        return False


def _consume_crc(file: BinaryIO, length: int, checksum: int) -> int | None:
    """Read a block body in bounded chunks while extending its CRC32."""
    remaining = length
    while remaining:
        chunk = file.read(min(remaining, 1024 * 1024))
        if not chunk:
            return None
        checksum = zlib.crc32(chunk, checksum)
        remaining -= len(chunk)
    return checksum


def _output_limit(block_type: int) -> int | None:
    if block_type in _METADATA_TYPES:
        return _MAX_METADATA_BYTES
    if block_type == _THUMBNAIL:
        return _MAX_THUMBNAIL_BYTES
    return None


def _parse_file_header(file: BinaryIO) -> int | None:
    header = file.read(10)
    if len(header) < 10 or header[:4] != MAGIC:
        return None
    _version, checksum_type = struct.unpack_from("<IH", header, 4)
    return checksum_type


def _block_param_len(block_type: int) -> int:
    if block_type == _THUMBNAIL:
        return 6
    if block_type in _METADATA_TYPES or block_type == _GCODE:
        return 2
    return 0


def _walk(file: BinaryIO, wanted: frozenset[int]) -> Iterator[_Block]:
    """Yield selected blocks while seeking past G-code and other block bodies."""
    checksum_type = _parse_file_header(file)
    if checksum_type is None:
        return

    for _ in range(_MAX_BLOCKS):
        block_header = file.read(8)
        if len(block_header) < 8:
            return
        block_type, compression, uncompressed_size = struct.unpack("<HHI", block_header)

        if compression == _COMP_NONE:
            data_len = uncompressed_size
        else:
            raw_size = file.read(4)
            if len(raw_size) < 4:
                return
            (data_len,) = struct.unpack("<I", raw_size)

        param_len = _block_param_len(block_type)
        params = file.read(param_len)
        if len(params) < param_len:
            return

        if block_type in wanted:
            if data_len > _MAX_BLOCK_DATA:
                return
            data = file.read(data_len)
            if len(data) < data_len:
                return
            yield block_type, compression, params, data
        else:
            file.seek(data_len, 1)

        if checksum_type != 0:
            file.seek(4, 1)


def _decompress(
    compression: int,
    data: bytes,
    *,
    max_output: int = _MAX_METADATA_BYTES,
) -> bytes | None:
    """Decode a block without accepting oversized or partial zlib streams."""
    if compression == _COMP_NONE:
        return data if len(data) <= max_output else None
    if compression != _COMP_DEFLATE:
        return None

    try:
        decompressor = zlib.decompressobj()
        output = decompressor.decompress(data, max_output + 1)
        if len(output) > max_output or decompressor.unconsumed_tail:
            return None
        output += decompressor.flush(max_output + 1 - len(output))
        if (
            len(output) > max_output
            or not decompressor.eof
            or decompressor.unused_data
            or decompressor.unconsumed_tail
        ):
            return None
        return output
    except zlib.error:
        return None


def read_metadata_text(path: Path) -> str | None:
    """Render binary metadata blocks as G-code-style comment lines."""
    lines: list[str] = []
    total = 0
    try:
        with path.open("rb") as file:
            for block_type, compression, _params, data in _walk(file, _METADATA_TYPES):
                raw = _decompress(
                    compression,
                    data,
                    max_output=_MAX_METADATA_BYTES,
                )
                if raw is None:
                    continue
                total += len(raw)
                if total > _MAX_METADATA_BYTES:
                    break
                text = raw.decode("utf-8", errors="replace")
                for metadata_line in text.splitlines():
                    metadata_line = metadata_line.strip()
                    if not metadata_line:
                        continue
                    lines.append("; " + metadata_line)
                    if (
                        block_type == _FILE_METADATA
                        and metadata_line.lower().startswith("producer=")
                    ):
                        producer = metadata_line.split("=", 1)[1].strip()
                        lines.append("; generated by " + producer)
    except OSError as error:
        logger.warning("bgcode: cannot read metadata %s: %s", path, error)
        return None

    if not lines:
        return None
    return "\n".join(lines) + "\n"


def iter_thumbnails(path: Path) -> Iterator[tuple[int, int, int, bytes]]:
    """Yield ``(format, width, height, image_bytes)`` thumbnail records."""
    try:
        with path.open("rb") as file:
            for _block_type, compression, params, data in _walk(
                file, frozenset({_THUMBNAIL})
            ):
                raw = _decompress(
                    compression,
                    data,
                    max_output=_MAX_THUMBNAIL_BYTES,
                )
                if raw is None or len(params) < 6:
                    continue
                image_format, width, height = struct.unpack_from("<HHH", params, 0)
                yield image_format, width, height, raw
    except OSError as error:
        logger.warning("bgcode: cannot read thumbnails %s: %s", path, error)
        return
