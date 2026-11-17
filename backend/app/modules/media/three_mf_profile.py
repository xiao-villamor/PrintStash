"""A bounded projection of Bambu/Orca project settings, without executing G-code.

Only the two named metadata members are read. Values describe the saved project,
not effective per-object overrides, measured results, or printer compatibility.
Unknown dialects and missing estimates are never filled from provider metadata.
"""

from __future__ import annotations

import json
import math
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.modules.media import three_mf_package as package

PROJECT_SETTINGS = "Metadata/project_settings.config"
SLICE_INFO = "Metadata/slice_info.config"
MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 64
MAX_TEXT = 512


class ProfileFailure(StrEnum):
    MALFORMED = "project_profile_malformed"
    LIMITS = "project_profile_limits"


class ProfileError(Exception):
    def __init__(self, failure: ProfileFailure):
        self.failure = failure
        super().__init__(failure.value)


@dataclass(frozen=True)
class PlateEstimate:
    number: int
    estimated_seconds: float | None
    filament_grams: float | None


@dataclass(frozen=True)
class ProjectProfile:
    name: str | None
    printer: str | None
    materials: tuple[str, ...]
    nozzle_diameters_mm: tuple[float, ...]
    layer_height_mm: float | None
    wall_loops: int | None
    infill_percent: float | None
    plates: tuple[PlateEstimate, ...]


def _text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileError(ProfileFailure.MALFORMED)
    if len(value) > MAX_TEXT:
        raise ProfileError(ProfileFailure.LIMITS)
    return value.strip() or None


def _number(value: object, *, maximum: float) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, float, int)):
        raise ProfileError(ProfileFailure.MALFORMED)
    if isinstance(value, str) and len(value) > MAX_TEXT:
        raise ProfileError(ProfileFailure.LIMITS)
    try:
        result = float(value)
    except ValueError:
        raise ProfileError(ProfileFailure.MALFORMED) from None
    if not math.isfinite(result) or not 0 <= result <= maximum:
        raise ProfileError(ProfileFailure.MALFORMED)
    return result


def _items(value: object) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProfileError(ProfileFailure.MALFORMED)
    if len(value) > MAX_ITEMS:
        raise ProfileError(ProfileFailure.LIMITS)
    return value


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError(ProfileFailure.MALFORMED)
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise ProfileError(ProfileFailure.MALFORMED)


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if (
        info.file_size > MAX_BYTES
        or info.file_size > max(info.compress_size, 1) * package.MAX_COMPRESSION_RATIO
    ):
        raise ProfileError(ProfileFailure.LIMITS)
    with archive.open(info) as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) != info.file_size:
        raise ProfileError(ProfileFailure.MALFORMED)
    return data


def _plates(data: bytes) -> tuple[PlateEstimate, ...]:
    import lxml.etree as etree

    if package.declares_dtd(data):
        raise ProfileError(ProfileFailure.MALFORMED)
    try:
        root = etree.fromstring(
            data,
            etree.XMLParser(
                resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
            ),
        )
    except etree.XMLSyntaxError:
        raise ProfileError(ProfileFailure.MALFORMED) from None
    if root.tag != "config":
        raise ProfileError(ProfileFailure.MALFORMED)
    elements = root.findall("plate")
    if len(elements) > MAX_ITEMS:
        raise ProfileError(ProfileFailure.LIMITS)
    result = []
    seen = set()
    for element in elements:
        values = _object(
            [
                (item.get("key"), item.get("value"))
                for item in element.findall("metadata")
                if item.get("key") in {"index", "prediction", "weight"}
            ]
        )
        number = _number(values.get("index"), maximum=MAX_ITEMS)
        if number is None or number < 1 or not number.is_integer() or number in seen:
            raise ProfileError(ProfileFailure.MALFORMED)
        seen.add(number)
        result.append(
            PlateEstimate(
                number=int(number),
                estimated_seconds=_number(
                    values.get("prediction"), maximum=1_000_000_000
                ),
                filament_grams=_number(values.get("weight"), maximum=1_000_000_000),
            )
        )
    return tuple(sorted(result, key=lambda plate: plate.number))


def read_profile(path: Path) -> ProjectProfile | None:
    """Read explicit settings, or None when the recognized metadata is absent."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > package.MAX_ENTRIES:
                raise ProfileError(ProfileFailure.LIMITS)
            names = {entry.filename: entry for entry in entries}
            if len({name.casefold() for name in names}) != len(entries):
                raise ProfileError(ProfileFailure.MALFORMED)
            project = names.get(PROJECT_SETTINGS)
            if project is None:
                return None
            settings = json.loads(
                _read(archive, project),
                object_pairs_hook=_object,
                parse_constant=_invalid_constant,
            )
            if not isinstance(settings, dict):
                raise ProfileError(ProfileFailure.MALFORMED)
            slices = names.get(SLICE_INFO)
            plates = _plates(_read(archive, slices)) if slices is not None else ()
        materials = tuple(
            material
            for item in _items(settings.get("filament_type"))
            if (material := _text(item)) is not None
        )
        nozzles = tuple(
            diameter
            for item in _items(settings.get("nozzle_diameter"))
            if (diameter := _number(item, maximum=100)) is not None
        )
        walls = _number(settings.get("wall_loops"), maximum=10_000)
        if walls is not None and not walls.is_integer():
            raise ProfileError(ProfileFailure.MALFORMED)
        infill = settings.get("sparse_infill_density")
        if isinstance(infill, str):
            infill = infill.removesuffix("%")
        return ProjectProfile(
            name=_text(settings.get("print_settings_id")),
            printer=_text(settings.get("printer_model")),
            materials=materials,
            nozzle_diameters_mm=nozzles,
            layer_height_mm=_number(settings.get("layer_height"), maximum=100),
            wall_loops=int(walls) if walls is not None else None,
            infill_percent=_number(infill, maximum=100),
            plates=plates,
        )
    except (
        zipfile.BadZipFile,
        ValueError,
        UnicodeError,
        RecursionError,
        RuntimeError,
        NotImplementedError,
    ):
        raise ProfileError(ProfileFailure.MALFORMED) from None
