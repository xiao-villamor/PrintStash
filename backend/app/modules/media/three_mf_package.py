"""A 3MF package's structure read without parsing XML: limits, root, tags.

Admission (``three_mf_bounds``) bounds a package before any worker exists, and
the worker (``three_mf_native``) decides whether lib3mf may read it. The claim
is only safe when both reach the same verdict, so both read the package
through these rules: the XML reader's archive envelope, its choice of root
part, and the model element's semantics.

Anything these rules cannot resolve with certainty raises :class:`Ambiguous`;
each caller then takes its conservative path.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

CORE_NS = b"http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRODUCTION_NS = b"http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
MODEL_RELATIONSHIP = b"http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
# Millimetres per model unit, as the XML reader scales them.
UNITS = {
    b"micron": 0.001,
    b"millimeter": 1.0,
    b"centimeter": 10.0,
    b"inch": 25.4,
    b"foot": 304.8,
    b"meter": 1000.0,
}
# The XML reader's own archive envelope; beyond it the reader refuses.
MAX_ENTRIES = 4096
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_RELATIONSHIP_BYTES = 1024 * 1024
CONTENT_TYPES = "[Content_Types].xml"
PACKAGE_RELATIONSHIPS = "_rels/.rels"
# Structural start tags never contain "<", so a part is split at its last "<"
# and every complete tag lands in one window. A tag longer than the carry is
# not something a producer writes.
_CHUNK_BYTES = 1024 * 1024
_MAX_CARRY_BYTES = 64 * 1024
_ATTRIBUTE = re.compile(
    rb"""\s(?:([A-Za-z_][\w.-]*):)?([A-Za-z_][\w.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)
_RELATIONSHIP = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?Relationship(?=[\s/>])([^>]*)>")

Attributes = dict[tuple[bytes | None, bytes], bytes]


class Ambiguous(Exception):
    """The package's structure cannot be read with certainty."""


@dataclass(frozen=True)
class ModelElement:
    """What one part's ``<model>`` start tag declares.

    ``plain`` holds when the part is core 3MF in a known unit and requires no
    extension beyond production: the semantics lib3mf and the XML reader share.
    """

    production_prefixes: frozenset[bytes]
    unit: bytes
    plain: bool


def attributes(raw: bytes) -> Attributes:
    values: Attributes = {}
    for match in _ATTRIBUTE.finditer(raw):
        key = (match.group(1), match.group(2))
        if key in values:
            raise Ambiguous("duplicate attribute")
        values[key] = match.group(3) if match.group(3) is not None else match.group(4)
    return values


def model_element(prefix: bytes | None, attrs: Attributes) -> ModelElement:
    namespaces = {
        name: value
        for (attr_prefix, name), value in attrs.items()
        if attr_prefix == b"xmlns"
    }
    own = namespaces.get(prefix) if prefix else attrs.get((None, b"xmlns"))
    unit = attrs.get((None, b"unit"), b"millimeter")
    required = attrs.get((None, b"requiredextensions"), b"").split()
    return ModelElement(
        production_prefixes=frozenset(
            name for name, value in namespaces.items() if value == PRODUCTION_NS
        ),
        unit=unit,
        plain=own == CORE_NS
        and unit in UNITS
        and all(namespaces.get(name) in {CORE_NS, PRODUCTION_NS} for name in required),
    )


def declares_dtd(window: bytes) -> bool:
    return b"<!DOCTYPE" in window or b"<!ENTITY" in window


def model_parts(
    archive: zipfile.ZipFile, *, max_model_bytes: int
) -> tuple[dict[str, zipfile.ZipInfo], list[str]]:
    """Entries by name and the sorted ``.model`` parts, inside the envelope."""
    entries = archive.infolist()
    names = {entry.filename: entry for entry in entries}
    if (
        len(entries) > MAX_ENTRIES
        or len(names) != len(entries)
        or sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES
    ):
        raise Ambiguous("archive limits")
    models = sorted(name for name in names if name.lower().endswith(".model"))
    if (
        not models
        or sum(names[name].file_size for name in models) > max_model_bytes
        or any(
            names[name].file_size
            > max(names[name].compress_size, 1) * MAX_COMPRESSION_RATIO
            for name in models
        )
    ):
        raise Ambiguous("model limits")
    return names, models


def is_opc(names: dict[str, zipfile.ZipInfo]) -> bool:
    """Whether the archive declares content types and package relationships.

    Every producer writes both; lib3mf refuses a package without them, while
    the XML reader falls back to the conventional root part.
    """
    return CONTENT_TYPES in names and PACKAGE_RELATIONSHIPS in names


def root_part(
    archive: zipfile.ZipFile, names: dict[str, zipfile.ZipInfo], models: list[str]
) -> str:
    """The part the XML reader treats as the package root."""
    relationship = names.get(PACKAGE_RELATIONSHIPS)
    if relationship is None:
        return next(
            (name for name in models if name.lower() == "3d/3dmodel.model"), models[0]
        )
    if relationship.file_size > MAX_RELATIONSHIP_BYTES:
        raise Ambiguous("relationship size")
    targets = []
    for match in _RELATIONSHIP.finditer(archive.read(relationship)):
        attrs = attributes(match.group(1))
        if attrs.get((None, b"Type")) == MODEL_RELATIONSHIP:
            if attrs.get((None, b"TargetMode"), b"Internal") != b"Internal":
                raise Ambiguous("external root")
            target = attrs.get((None, b"Target"))
            if target is None:
                raise Ambiguous("relationship without a target")
            targets.append(target.decode("utf-8", "strict"))
    if len(targets) != 1:
        raise Ambiguous("root relationship")
    (target,) = targets
    if "\\" in target or ":" in target or ".." in PurePosixPath(target).parts:
        raise Ambiguous("unsafe root")
    if target.lstrip("/") not in models:
        raise Ambiguous("root relationship")
    return target.lstrip("/")


def linked_parts(
    archive: zipfile.ZipFile, names: dict[str, zipfile.ZipInfo], part: str
) -> frozenset[str]:
    """Model parts *part* declares in its own relationships.

    lib3mf loads only the parts the root part links this way; the XML reader
    follows a production path to any part in the archive.
    """
    directory, _, base = part.rpartition("/")
    relationship = names.get(
        f"{directory}/_rels/{base}.rels" if directory else f"_rels/{base}.rels"
    )
    if relationship is None:
        return frozenset()
    if relationship.file_size > MAX_RELATIONSHIP_BYTES:
        raise Ambiguous("relationship size")
    targets = set()
    for match in _RELATIONSHIP.finditer(archive.read(relationship)):
        attrs = attributes(match.group(1))
        target = attrs.get((None, b"Target"))
        if attrs.get((None, b"Type")) == MODEL_RELATIONSHIP and target is not None:
            targets.add(target.decode("utf-8", "strict").lstrip("/"))
    return frozenset(targets)


def windows(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> Iterator[bytes]:
    """One part's bytes in windows that each end before an unfinished tag.

    ``checkpoint`` runs before each window. A caller may stop early; one that
    reads to the end has had the whole part, or :class:`Ambiguous` is raised.
    """
    carry = b""
    total = 0
    with archive.open(info) as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            total += len(chunk)
            if checkpoint is not None:
                checkpoint()
            window = carry + chunk
            cut = window.rfind(b"<")
            if cut == -1:
                cut = len(window)
            # A window that is one unfinished tag stays whole in the carry.
            yield window[:cut]
            carry = window[cut:]
            if len(carry) > _MAX_CARRY_BYTES:
                raise Ambiguous("unterminated tag")
    if checkpoint is not None:
        checkpoint()
    yield carry
    if total != info.file_size:
        raise Ambiguous("incomplete part")
