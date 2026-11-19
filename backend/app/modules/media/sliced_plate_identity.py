"""Read explicit associations between a sliced plate and its G-code member.

Names and object counts can help associate a sliced plate with a source plate;
these are identity hints, not proof that the sliced geometry is unchanged.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import lxml.etree as etree

from app.core.native_settings import settings
from app.modules.media import three_mf_package as package
from app.modules.media.three_mf_preview import _open_archive, preview_capacity

_MODEL_SETTINGS = "Metadata/model_settings.config"
_SLICE_INFO = "Metadata/slice_info.config"
_GCODE = re.compile(r"^Metadata/plate_([1-9][0-9]{0,8})\.gcode$")
_MAX_BYTES = 16 * 1024 * 1024
_MAX_PLATES = 64
_MAX_NAME = 1024


@dataclass(frozen=True)
class SlicedPlateIdentity:
    number: int
    name: str
    object_count: int


def _xml(archive: zipfile.ZipFile, name: str) -> etree._Element | None:
    entries = [info for info in archive.infolist() if info.filename == name]
    if len(entries) != 1:
        return None
    info = entries[0]
    if (
        info.file_size > _MAX_BYTES
        or info.file_size > max(info.compress_size, 1) * package.MAX_COMPRESSION_RATIO
    ):
        return None
    data = archive.read(info)
    if len(data) != info.file_size or package.declares_dtd(data):
        return None
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
    )
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError:
        return None


def _fields(plate: etree._Element) -> dict[str, str] | None:
    result: dict[str, str] = {}
    for item in plate.findall("metadata"):
        key, value = item.get("key"), item.get("value")
        if key is None or value is None:
            continue
        if key in result:
            return None
        result[key] = value
    return result


def read_sliced_plate_identities(path: Path) -> tuple[SlicedPlateIdentity, ...]:
    """Return only unambiguous named plates explicitly linked to G-code.

    Invalid or missing optional slicer metadata cannot prevent toolpath preview.
    Limits and archive validation follow the embedded G-code preview path.
    """
    with (
        preview_capacity(),
        _open_archive(
            path,
            archive_cap=settings.three_mf_preview_max_archive_mb * 1024 * 1024,
            entry_cap=settings.three_mf_preview_max_entries,
            central_cap=settings.three_mf_preview_max_central_directory_mb
            * 1024
            * 1024,
        ) as archive,
    ):
        model = _xml(archive, _MODEL_SETTINGS)
        sliced = _xml(archive, _SLICE_INFO)
        if model is None or sliced is None:
            return ()
        model_plates = model.findall("plate")
        slice_plates = sliced.findall("plate")
        if len(model_plates) > _MAX_PLATES or len(slice_plates) > _MAX_PLATES:
            return ()
        counts: dict[int, int] = {}
        duplicate_counts: set[int] = set()
        for plate in slice_plates:
            fields = _fields(plate)
            if fields is None:
                continue
            index = fields.get("index")
            if (
                index is None
                or not index.isdecimal()
                or len(index) > 9
                or int(index) < 1
            ):
                continue
            number = int(index)
            objects = plate.findall("object")
            count = sum(obj.get("skipped") != "true" for obj in objects)
            if number in counts:
                duplicate_counts.add(number)
            counts[number] = count
        result: list[SlicedPlateIdentity] = []
        for plate in model_plates:
            fields = _fields(plate)
            if fields is None:
                continue
            name, gcode = fields.get("plater_name"), fields.get("gcode_file")
            match = _GCODE.fullmatch(gcode) if gcode is not None else None
            if (
                match is None
                or name is None
                or not name.strip()
                or len(name) > _MAX_NAME
            ):
                continue
            number = int(match.group(1))
            plater_id = fields.get("plater_id")
            if plater_id is None or not plater_id.isdecimal() or len(plater_id) > 9:
                continue
            declared_number = int(plater_id)
            if (
                declared_number in duplicate_counts
                or counts.get(declared_number, 0) < 1
            ):
                continue
            if sum(info.filename == gcode for info in archive.infolist()) != 1:
                continue
            result.append(
                SlicedPlateIdentity(number, name.strip(), counts[declared_number])
            )
        # Duplicate associations or names are ambiguous and cannot be matched safely.
        return tuple(
            entry
            for entry in result
            if sum(other.number == entry.number for other in result) == 1
            and sum(other.name == entry.name for other in result) == 1
        )
