"""Read a single explicit display color for a 3MF viewer mesh."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from lxml import etree

from app.modules.media import three_mf_package as package
from app.modules.media.three_mf_profile import PROJECT_SETTINGS

STL_COLOR_PREFIX = b"PrintStash RGB:"
_CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
_MATERIAL = "{http://schemas.microsoft.com/3dmanufacturing/material/2015/02}"
_MAX_METADATA_BYTES = 8 * 1024 * 1024
_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?")


def _rgb(value: object) -> bytes | None:
    if isinstance(value, str) and _HEX_COLOR.fullmatch(value):
        return bytes.fromhex(value[1:7])
    return None


def _read(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> bytes:
    if (
        member.file_size > _MAX_METADATA_BYTES
        or member.file_size
        > max(member.compress_size, 1) * package.MAX_COMPRESSION_RATIO
    ):
        raise package.Ambiguous("preview color metadata size")
    return archive.read(member)


def _model_colors(data: bytes) -> set[bytes | None]:
    """Resolve properties used by built meshes, including triangle overrides."""
    if package.declares_dtd(data):
        raise package.Ambiguous("preview color XML declares DTD")
    root = etree.fromstring(
        data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    )
    groups = {}
    objects = {}
    for resource in root.findall(f"{_CORE}resources/*"):
        identifier = resource.get("id")
        if resource.tag == f"{_CORE}object":
            objects[identifier] = resource
        elif resource.tag == f"{_CORE}basematerials":
            groups[identifier] = [_rgb(base.get("displaycolor")) for base in resource]
        elif resource.tag == f"{_MATERIAL}colorgroup":
            groups[identifier] = [_rgb(color.get("color")) for color in resource]

    colors: set[bytes | None] = set()
    pending = list(root.findall(f"{_CORE}build/{_CORE}item"))
    seen = set()
    while pending:
        instance = pending.pop()
        identifier = instance.get("objectid")
        if any(key.endswith("}path") for key in instance.attrib):
            # The slicer palette can still supply a uniform project color.
            return set()
        if identifier in seen:
            continue
        seen.add(identifier)
        mesh_object = objects.get(identifier)
        if mesh_object is None:
            return set()
        pending.extend(mesh_object.findall(f"{_CORE}components/{_CORE}component"))
        default_pid = mesh_object.get("pid")
        default_index = mesh_object.get("pindex")
        for triangle in mesh_object.iterfind(
            f"{_CORE}mesh/{_CORE}triangles/{_CORE}triangle"
        ):
            pid = triangle.get("pid", default_pid)
            p1 = triangle.get("p1", default_index)
            if pid is None and p1 is None:
                colors.add(None)
                continue
            palette = groups.get(pid, [])
            for index in (p1, triangle.get("p2", p1), triangle.get("p3", p1)):
                offset = int(index) if index is not None else -1
                colors.add(palette[offset] if 0 <= offset < len(palette) else None)
            if len(colors) > 1:
                return colors
    return colors


def viewer_color(path: Path) -> bytes | None:
    """Return RGB only when an explicit material or slicer palette is uniform."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > package.MAX_ENTRIES:
                return None
            names = {entry.filename: entry for entry in entries}
            if len({name.casefold() for name in names}) != len(entries):
                return None
            models = [name for name in names if name.lower().endswith(".model")]
            if models:
                root = package.root_part(archive, names, models)
                if names[root].file_size <= _MAX_METADATA_BYTES:
                    colors = _model_colors(_read(archive, names[root]))
                    if colors - {None}:
                        return next(iter(colors)) if len(colors) == 1 else None
            settings_member = names.get(PROJECT_SETTINGS)
            if settings_member is None:
                return None
            settings = json.loads(_read(archive, settings_member))
            values = (
                settings.get("filament_colour") if isinstance(settings, dict) else None
            )
            if not isinstance(values, list) or not 1 <= len(values) <= 64:
                return None
            palette = {_rgb(value) for value in values}
            return next(iter(palette)) if len(palette) == 1 else None
    except (
        OSError,
        zipfile.BadZipFile,
        package.Ambiguous,
        etree.XMLSyntaxError,
        ValueError,
        RecursionError,
        RuntimeError,
    ):
        # Color is optional viewer evidence. Keep valid geometry drawable.
        return None
