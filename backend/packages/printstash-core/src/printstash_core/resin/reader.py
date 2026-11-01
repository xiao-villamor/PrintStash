"""Read CTB v2–5 and GOO V3.0 headers without decoding print layers.

Layouts: elegooofficial/GOO (Goo Format Spec V1.2) and
cbiffle/catibo (doc/cbddlp-ctb.adoc), with CTB v3–5 header compatibility
documented by sn4k3/UVtools. Preview decoding is bounded independently of
the number or resolution of print layers. Nothing rewrites printer bytes.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO

_MAX_PIXELS = 4_194_304
_GOO_PREVIEW_OFFSET = 27_112
_GOO_METADATA_OFFSET = 195_314


class ResinFormatError(ValueError):
    """The header or preview is unsupported, truncated, or internally invalid."""


class ResinFormat(str, Enum):
    CTB = "ctb"
    GOO = "goo"


@dataclass(frozen=True)
class ResinMetadata:
    layer_height_mm: float
    layer_count: int
    exposure_time_s: float
    estimated_time_s: int
    resin_volume_ml: float | None
    printer_model: str | None
    slicer_name: str | None
    slicer_version: str | None


@dataclass(frozen=True)
class ResinPreview:
    width: int
    height: int
    rgb: bytes


def _read(source: BinaryIO, offset: int, size: int) -> bytes:
    source.seek(offset)
    data = source.read(size)
    if len(data) != size:
        raise ResinFormatError("truncated_resin_file")
    return data


def _format(source: BinaryIO) -> ResinFormat:
    prefix = _read(source, 0, 12)
    if prefix[4:] == b"\x07\x00\x00\x00DLP\x00":
        if prefix[:4] != b"V3.0":
            raise ResinFormatError("unsupported_goo_version")
        return ResinFormat.GOO
    magic, version = struct.unpack_from("<II", prefix)
    if magic not in (0x12FD0086, 0x12FD0106):
        raise ResinFormatError("unsupported_resin_format")
    if version not in (2, 3, 4, 5):
        raise ResinFormatError("unsupported_ctb_version")
    return ResinFormat.CTB


def _number(value: float, *, positive: bool = False) -> float:
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ResinFormatError("invalid_resin_metadata")
    return value


def _text(value: bytes) -> str | None:
    try:
        text = value.split(b"\x00", 1)[0].decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ResinFormatError("invalid_resin_text") from exc
    return text or None


def read_metadata(path: Path) -> ResinMetadata:
    with path.open("rb") as source:
        kind = _format(source)
        if kind is ResinFormat.GOO:
            header = _read(source, 0, 198)
            facts = _read(source, _GOO_METADATA_OFFSET, 144)
            count = struct.unpack_from(">I", facts)[0]
            height, exposure = struct.unpack_from(">ff", facts, 22)
            seconds = struct.unpack_from(">I", facts, 136)[0]
            volume = _number(struct.unpack_from(">f", facts, 140)[0]) / 1000
            printer = _text(header[92:124])
            slicer = _text(header[12:44])
            version = _text(header[44:68])
        else:
            header = _read(source, 0, 112)
            height, exposure = struct.unpack_from("<ff", header, 32)
            count = struct.unpack_from("<I", header, 68)[0]
            seconds = struct.unpack_from("<I", header, 76)[0]
            config_offset, config_size = struct.unpack_from("<II", header, 84)
            volume = None
            if config_offset:
                if config_size < 24:
                    raise ResinFormatError("invalid_ctb_config")
                volume = _number(
                    struct.unpack("<f", _read(source, config_offset + 20, 4))[0]
                )
            info_offset, info_size = struct.unpack_from("<II", header, 104)
            printer = None
            if info_offset:
                if info_size < 36:
                    raise ResinFormatError("invalid_ctb_config")
                info = _read(source, info_offset + 28, 8)
                name_offset, name_size = struct.unpack("<II", info)
                if name_size > 128:
                    raise ResinFormatError("invalid_resin_text")
                if name_size:
                    printer = _text(_read(source, name_offset, name_size))
            # CTB can be written by multiple slicers; its format version does
            # not identify the software that produced it.
            slicer = version = None
        if count == 0:
            raise ResinFormatError("invalid_resin_metadata")
        return ResinMetadata(
            layer_height_mm=_number(height, positive=True),
            layer_count=count,
            exposure_time_s=_number(exposure),
            estimated_time_s=seconds,
            resin_volume_ml=volume,
            printer_model=printer,
            slicer_name=slicer,
            slicer_version=version,
        )


def _rgb(pixel: int, *, green_bits: int) -> bytes:
    red = (pixel >> 11) & 31
    green = (pixel >> (11 - green_bits)) & ((1 << green_bits) - 1)
    blue = pixel & 31
    return bytes(
        (red * 255 // 31, green * 255 // ((1 << green_bits) - 1), blue * 255 // 31)
    )


def extract_preview(path: Path) -> ResinPreview | None:
    with path.open("rb") as source:
        kind = _format(source)
        if kind is ResinFormat.GOO:
            encoded = _read(source, _GOO_PREVIEW_OFFSET, 290 * 290 * 2)
            rgb = b"".join(
                _rgb(pixel, green_bits=6)
                for (pixel,) in struct.iter_unpack(">H", encoded)
            )
            return ResinPreview(290, 290, rgb)
        header = _read(source, 0, 112)
        large = struct.unpack_from("<I", header, 60)[0]
        small = struct.unpack_from("<I", header, 72)[0]
        offset = large or small
        if offset == 0:
            return None
        width, height, data_offset, data_size = struct.unpack(
            "<IIII", _read(source, offset, 16)
        )
        pixels = width * height
        if not width or not height or pixels > _MAX_PIXELS:
            raise ResinFormatError("invalid_resin_preview_dimensions")
        if data_size % 2 or data_size > pixels * 4:
            raise ResinFormatError("invalid_resin_preview_size")
        encoded = _read(source, data_offset, data_size)
        rgb = bytearray()
        cursor = 0
        while cursor < len(encoded):
            pixel = struct.unpack_from("<H", encoded, cursor)[0]
            cursor += 2
            count = 1
            if pixel & 0x20:
                if cursor == len(encoded):
                    raise ResinFormatError("truncated_resin_preview_run")
                run = struct.unpack_from("<H", encoded, cursor)[0]
                cursor += 2
                if run & 0xF000 != 0x3000:
                    raise ResinFormatError("invalid_resin_preview_run")
                count += run & 0xFFF
            if len(rgb) + count * 3 > pixels * 3:
                raise ResinFormatError("resin_preview_overflow")
            rgb.extend(_rgb(pixel, green_bits=5) * count)
        if len(rgb) != pixels * 3:
            raise ResinFormatError("resin_preview_underflow")
        return ResinPreview(width, height, bytes(rgb))
