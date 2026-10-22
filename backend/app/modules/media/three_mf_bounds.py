"""Upper bounds on a 3MF package's geometry, computed without parsing its XML.

Native admission needs a claim before the guarded worker reads the package. A
full parse in the supervisor would cost what the worker is about to spend, so
this module streams each ``.model`` part once and only recognizes structure:
``object``, ``component``, ``item`` and ``build`` start tags, plus every
occurrence of the word ``triangle``. That count can only exceed the triangles
an object declares (comments, metadata and the ``triangles`` container also
match), never fall short of it, so a weighted claim built on it never starves
the worker.

Anything this scanner cannot resolve with certainty returns ``None`` and keeps
the whole-pool claim: an unsafe or ambiguous package is the guarded reader's to
refuse, not this estimate's to guess.
"""

from __future__ import annotations

import re
import threading
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.modules.media import three_mf_package as package

MAX_DEPTH = 64
_STRUCTURE = re.compile(
    rb"<(?:([A-Za-z_][\w.-]*):)?(model|object|component|item|build)(?=[\s/>])([^>]*)>"
)
_STRUCTURAL_WORDS = (b"model", b"object", b"component", b"item", b"build")
_CACHE_ENTRIES = 256


@dataclass(frozen=True)
class ThreeMFBounds:
    """Bounds the guarded reader cannot exceed for one package.

    ``reached_model_bytes`` is the uncompressed size of the root part and of
    every part a reachable object lives in: the reader parses exactly those.
    ``placed_faces`` counts triangles once per placement reachable from a build
    item, through components, capped at ``face_cap + 1``.
    """

    reached_model_bytes: int
    placed_faces: int
    lib3mf_readable: bool


@dataclass(frozen=True)
class _Semantics:
    """What decides whether lib3mf reads a part exactly as the XML reader would."""

    plain: bool
    unit: bytes
    has_build: bool


@dataclass
class _Object:
    faces: int
    children: list[tuple[str, int]]


_CACHE: OrderedDict[tuple[object, ...], ThreeMFBounds | None] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_Unresolvable = package.Ambiguous


def _object_id(value: bytes | None) -> int:
    if value is None or not value.isdigit() or not 1 <= int(value) <= 2**31 - 1:
        raise _Unresolvable("object id")
    return int(value)


def _target(part: str, attrs: package.Attributes, production: frozenset[bytes]) -> str:
    # The reader follows only the production namespace's path; any other
    # "path" attribute leaves the reference inside its own part.
    paths = [
        value
        for (prefix, name), value in attrs.items()
        if name == b"path" and prefix in production
    ]
    if not paths:
        return part
    (path,) = paths
    target = path.decode("utf-8", "strict").lstrip("/")
    if "\\" in target or ".." in PurePosixPath(target).parts:
        raise _Unresolvable("unsafe path")
    return target


class _Budget:
    def __init__(self, wall_seconds: float, cpu_seconds: float) -> None:
        self.deadline = time.monotonic() + wall_seconds
        self.cpu_deadline = time.thread_time() + cpu_seconds

    def check(self) -> None:
        if time.monotonic() >= self.deadline or time.thread_time() >= self.cpu_deadline:
            raise _Unresolvable("preflight budget")


def _scan_part(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    budget: _Budget,
) -> tuple[dict[int, _Object], list[tuple[str, int]], _Semantics]:
    """Objects and build items of one part, read in bounded windows."""
    name = info.filename
    objects: dict[int, _Object] = {}
    items: list[tuple[str, int]] = []
    model: package.ModelElement | None = None
    dtd = has_build = False
    current: _Object | None = None

    def count(window: bytes, start: int = 0, end: int | None = None) -> None:
        stray = window.count(b"triangle", start, len(window) if end is None else end)
        if stray:
            if current is None:
                raise _Unresolvable("triangles outside an object")
            current.faces += stray

    def scan(window: bytes) -> None:
        nonlocal current, model, dtd, has_build
        dtd = dtd or package.declares_dtd(window)
        # Mesh windows hold only vertex and triangle tags. A window without any
        # structural word has no structural match, so counting is all it needs.
        if model is not None and not any(word in window for word in _STRUCTURAL_WORDS):
            count(window)
            return
        position = 0
        for match in _STRUCTURE.finditer(window):
            count(window, position, match.start())
            position = match.end()
            prefix, tag, raw = match.group(1), match.group(2), match.group(3)
            attrs = package.attributes(raw)
            if tag == b"model":
                if model is not None:
                    raise _Unresolvable("second model element")
                model = package.model_element(prefix, attrs)
                continue
            if model is None or any(
                attr_prefix == b"xmlns"
                or (attr_prefix is None and attr_name == b"xmlns")
                for attr_prefix, attr_name in attrs
            ):
                raise _Unresolvable("namespace outside the model element")
            if tag == b"build":
                has_build = True
            elif tag == b"object":
                object_id = _object_id(attrs.get((None, b"id")))
                if object_id in objects:
                    raise _Unresolvable("duplicate object")
                current = objects[object_id] = _Object(0, [])
            else:
                reference = (
                    _target(name, attrs, model.production_prefixes),
                    _object_id(attrs.get((None, b"objectid"))),
                )
                if tag == b"item":
                    items.append(reference)
                elif current is None:
                    raise _Unresolvable("component outside an object")
                else:
                    current.children.append(reference)
        count(window, position)

    for window in package.windows(archive, info, checkpoint=budget.check):
        scan(window)
    if model is None:
        raise _Unresolvable("part without a model element")
    return (
        objects,
        items,
        _Semantics(plain=model.plain and not dtd, unit=model.unit, has_build=has_build),
    )


def bounds(
    path: Path,
    *,
    face_cap: int,
    max_model_bytes: int,
    wall_seconds: float,
    cpu_seconds: float,
) -> ThreeMFBounds | None:
    """Upper bounds for *path*, or ``None`` when they cannot be certain in budget.

    A package's metadata and thumbnail derivatives each ask for a claim; the
    answer for an unchanged file is remembered so its parts are inflated once.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (
        str(path),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        face_cap,
        max_model_bytes,
    )
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    measured = _bounds(
        path,
        face_cap=face_cap,
        max_model_bytes=max_model_bytes,
        wall_seconds=wall_seconds,
        cpu_seconds=cpu_seconds,
    )
    with _CACHE_LOCK:
        _CACHE[key] = measured
        while len(_CACHE) > _CACHE_ENTRIES:
            _CACHE.popitem(last=False)
    return measured


def forget_bounds() -> None:
    """Drop every remembered answer."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _bounds(
    path: Path,
    *,
    face_cap: int,
    max_model_bytes: int,
    wall_seconds: float,
    cpu_seconds: float,
) -> ThreeMFBounds | None:
    budget = _Budget(wall_seconds, cpu_seconds)
    try:
        with zipfile.ZipFile(path) as archive:
            names, models = package.model_parts(
                archive, max_model_bytes=max_model_bytes
            )
            opc = package.is_opc(names)
            root = package.root_part(archive, names, models)
            linked = package.linked_parts(archive, names, root)
            parts = {name: _scan_part(archive, names[name], budget) for name in models}
    except (
        _Unresolvable,
        OSError,
        ValueError,
        EOFError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
    ):
        # Encrypted, truncated or unsupported entries are the reader's to refuse.
        return None

    cap = face_cap + 1
    placed: dict[tuple[str, int], int] = {}

    def faces(key: tuple[str, int], ancestors: tuple[tuple[str, int], ...]) -> int:
        if key in ancestors or len(ancestors) >= MAX_DEPTH:
            raise _Unresolvable("cyclic or deep component graph")
        if key in placed:
            return placed[key]
        part, object_id = key
        if part not in parts or object_id not in parts[part][0]:
            raise _Unresolvable("missing object")
        obj = parts[part][0][object_id]
        total = obj.faces
        for child in obj.children:
            total = min(cap, total + faces(child, (*ancestors, key)))
        placed[key] = total
        return total

    try:
        total = 0
        for item in parts[root][1]:
            total = min(cap, total + faces(item, ()))
    except _Unresolvable:
        return None
    reached = {root} | {part for part, _ in placed}
    semantics = [parts[name][2] for name in models]
    return ThreeMFBounds(
        sum(names[part].file_size for part in reached),
        total,
        # The worker reads through lib3mf only what lib3mf reads with the XML
        # reader's meaning: an OPC package whose reached parts the root links,
        # each with its own build, plain core semantics and one unit. Anything
        # else falls back to the XML reader, which this claim must then cover.
        lib3mf_readable=lib3mf_available()
        and opc
        and reached - {root} <= linked
        and all(part.plain and part.has_build for part in semantics)
        and len({part.unit for part in semantics}) == 1,
    )


def lib3mf_available() -> bool:
    """Whether the worker's lib3mf reader loads on this platform."""
    from app.modules.media import three_mf_native

    return three_mf_native.lib3mf_loaded()
