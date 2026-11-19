"""Bounded inspection of sliced 3MFs that contain toolpaths but no mesh."""

from __future__ import annotations

import math
import re
import warnings
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.native_settings import settings
from app.modules.media import three_mf_package as package
from app.modules.media.sliced_plate_identity import _fields, _xml
from app.modules.media.three_mf_preview import (
    EmbeddedGcodeError,
    _open_archive,
    _safe_member_name,
    list_embedded_gcode_plates,
    preview_capacity,
)


class SlicedGeometry(StrEnum):
    GCODE_ONLY = "gcode_only"


@dataclass(frozen=True)
class ArchiveImage:
    member: str
    plate: int | None
    media_type: str


@dataclass(frozen=True)
class SlicedArchive:
    geometry: SlicedGeometry
    metadata: dict[str, Any]
    plate_metadata: list[dict[str, Any]]
    images: tuple[ArchiveImage, ...]


_MAX_IMAGE_PIXELS = 25_000_000
_MAX_IMAGE_BYTES = 16 * 1024 * 1024
_IMAGES = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _archive(path: Path):
    return _open_archive(
        path,
        archive_cap=settings.three_mf_preview_max_archive_mb * 1024 * 1024,
        entry_cap=settings.three_mf_preview_max_entries,
        central_cap=settings.three_mf_preview_max_central_directory_mb * 1024 * 1024,
    )


def _number(value: str | None) -> float | None:
    try:
        number = float(value) if value is not None else None
    except ValueError:
        return None
    return (
        number if number is not None and math.isfinite(number) and number >= 0 else None
    )


def _pixel_area(
    archive: zipfile.ZipFile, item: zipfile.ZipInfo, media_type: str
) -> int:
    from PIL import Image

    try:
        with warnings.catch_warnings(), archive.open(item) as stream:
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(stream) as image:
                area = image.width * image.height
                return (
                    area
                    if 0 < area <= _MAX_IMAGE_PIXELS
                    and Image.MIME.get(image.format) == media_type
                    else 0
                )
    except (
        OSError,
        RuntimeError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        zipfile.BadZipFile,
    ):
        return 0


def _images(
    archive: zipfile.ZipFile, plates: tuple[int, ...]
) -> tuple[ArchiveImage, ...]:
    photos = []
    plate_images = {}
    for item in archive.infolist():
        name = item.filename
        media_type = _IMAGES.get(Path(name).suffix.lower())
        if (
            media_type is None
            or not _safe_member_name(name)
            or not 0 < item.file_size <= _MAX_IMAGE_BYTES
            or item.file_size
            > max(item.compress_size, 1) * package.MAX_COMPRESSION_RATIO
        ):
            continue
        is_photo = name.startswith("Auxiliaries/Model Pictures/")
        match = re.fullmatch(r"Metadata/(?:plate|top)_([1-9][0-9]{0,8})\.png", name)
        number = int(match[1]) if match else None
        if not is_photo and number not in plates:
            continue
        area = _pixel_area(archive, item, media_type)
        if area == 0:
            continue
        if is_photo:
            photos.append((area, ArchiveImage(name, None, media_type)))
        elif number is not None:
            prior = plate_images.get(number)
            if prior is None or area > prior[0]:
                plate_images[number] = (area, ArchiveImage(name, number, media_type))
    photos.sort(key=lambda item: (-item[0], item[1].member))
    return tuple(image for _, image in photos[:6]) + tuple(
        plate_images[number][1] for number in sorted(plate_images)
    )


def inspect_sliced_archive(path: Path) -> SlicedArchive | None:
    """Recognize metadata-only model parts and at least one actual G-code member."""
    plates = list_embedded_gcode_plates(path)
    if not plates:
        return None
    with preview_capacity(), _archive(path) as archive:
        try:
            _, parts = package.model_parts(archive, max_model_bytes=16 * 1024 * 1024)
        except package.Ambiguous:
            return None
        for part in parts:
            model = _xml(archive, part)
            if (
                model is None
                or not isinstance(model.tag, str)
                or model.tag.rsplit("}", 1)[-1] != "model"
                or model.find(".//{*}mesh") is not None
            ):
                return None
        sliced = _xml(archive, "Metadata/slice_info.config")
        fields = {}
        if sliced is not None:
            for plate in sliced.findall("plate"):
                values = _fields(plate)
                if values and (index := values.get("index")) is not None:
                    fields[index] = values
        metadata = []
        for plate in plates:
            values = fields.get(str(plate), {})
            duration = _number(values.get("prediction"))
            metadata.append(
                {
                    "number": plate,
                    "metadata": {
                        "estimated_time_s": round(duration)
                        if duration is not None and duration < 2**31
                        else None,
                        "filament_weight_g": _number(values.get("weight")),
                        "printer_model": values.get("printer_model_id"),
                        "nozzle_diameter_mm": _number(values.get("nozzle_diameters")),
                    },
                }
            )
        first = metadata[0]["metadata"]
        summary = {
            key: value
            for key, value in first.items()
            if (len(plates) == 1 or key in {"printer_model", "nozzle_diameter_mm"})
            and all(plate["metadata"][key] == value for plate in metadata)
        }
        return SlicedArchive(
            SlicedGeometry.GCODE_ONLY, summary, metadata, _images(archive, plates)
        )


def read_archive_image(path: Path, image: ArchiveImage) -> bytes:
    with preview_capacity(), _archive(path) as archive:
        try:
            item = archive.getinfo(image.member)
            if not 0 < item.file_size <= _MAX_IMAGE_BYTES:
                raise EmbeddedGcodeError("embedded_gcode_archive_too_large")
            return archive.read(item)
        except (KeyError, RuntimeError, zipfile.BadZipFile) as exc:
            raise EmbeddedGcodeError("embedded_gcode_not_found") from exc
