"""3MF scenes read through lib3mf, the 3MF Consortium's reference implementation.

lib3mf parses a package into compact native buffers instead of an XML tree, so
a scene costs a fraction of the XML reader's memory and time. Its coordinates
are single precision, the format's own interchange precision.

This reader only answers packages it can read with the XML reader's exact
semantics. Anything else raises :class:`NativeUnavailable` and the caller reads
the package with the XML reader, which owns every refusal and its
classification: required extensions, units, archive limits, damaged XML and
the production packages lib3mf rejects (non-root parts without a build).
"""

from __future__ import annotations

import ctypes
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_VERTICES
from printstash_core.mesh.similarity.components import (
    Assembly,
    ExpandedScene,
    Instance,
    MeshResource,
    expand_scene,
)

from app.core.native_settings import settings
from app.modules.media import three_mf_package as package

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

# Single precision steps 0.004 mm at 65536 mm. Geometry or placements farther
# from the origin than that lose shape in lib3mf's coordinates; the XML reader
# keeps them in double precision.
SINGLE_PRECISION_REACH_MM = 65536.0
# The XML reader's scene limits; beyond them it refuses the package itself.
MAX_RESOURCES = 4096
MAX_BUILD_ITEMS = 2048
_TAG = re.compile(rb"<(?:([A-Za-z_][\w.-]*):)?(model|item)(?=[\s/>])([^>]*)>")


class NativeUnavailable(Exception):
    """The package must be read by the XML reader."""


def _load_binding() -> tuple[Any, Any] | None:
    """lib3mf and its loaded library, or ``None`` where this platform lacks it.

    Loaded at import: the worker template preloads this module, so a forked
    worker inherits the mapped library instead of mapping it under its ceiling.
    """
    try:
        import lib3mf
    except ImportError:  # platforms without a lib3mf build
        return None
    try:
        return lib3mf, lib3mf.get_wrapper()
    except OSError, lib3mf.ELib3MFException:
        return None


_BINDING = _load_binding()


def lib3mf_loaded() -> bool:
    return _BINDING is not None


@dataclass(frozen=True)
class _Part:
    unit: bytes
    printable: tuple[bool, ...]


def _scan_part(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, whole: bool
) -> _Part:
    """The part's model element and, when *whole*, its build items' printability.

    A part that is not *whole* is read only through its first window: the model
    element opens the part, and a DTD can only precede it.
    """
    model: package.ModelElement | None = None
    printable: list[bool] = []
    for window in package.windows(archive, info):
        if package.declares_dtd(window):
            raise NativeUnavailable("document type declaration")
        for match in _TAG.finditer(window):
            prefix, tag, raw = match.group(1), match.group(2), match.group(3)
            attrs = package.attributes(raw)
            if tag == b"model":
                if model is not None:
                    raise NativeUnavailable("second model element")
                model = package.model_element(prefix, attrs)
                if not model.plain:
                    raise NativeUnavailable("model semantics")
            elif model is None:
                raise NativeUnavailable("structure before the model element")
            else:
                printable.append(
                    attrs.get((None, b"printable"), b"1") in (b"1", b"true")
                )
        if not whole:
            break
    if model is None:
        raise NativeUnavailable("model element beyond the first window")
    return _Part(model.unit, tuple(printable))


def _preflight(path: Path) -> tuple[float, tuple[bool, ...]]:
    """Unit and root build printability, or ``NativeUnavailable``."""
    try:
        with zipfile.ZipFile(path) as archive:
            names, models = package.model_parts(
                archive, max_model_bytes=settings.mesh_3mf_max_xml_mb * 1024 * 1024
            )
            if not package.is_opc(names):
                raise NativeUnavailable("not an OPC package")
            root = package.root_part(archive, names, models)
            parts = {
                name: _scan_part(archive, names[name], whole=name == root)
                for name in models
            }
    except (
        package.Ambiguous,
        OSError,
        ValueError,
        EOFError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
    ) as exc:
        raise NativeUnavailable("unreadable package") from exc
    # The XML reader scales each part by its own unit; lib3mf by the root's.
    if len({part.unit for part in parts.values()}) != 1:
        raise NativeUnavailable("mixed units")
    return package.UNITS[parts[root].unit], parts[root].printable


def _transform(fields: Any, unit: float) -> NDArray[np.float64]:
    import numpy as np

    transform = np.eye(4)
    transform[:3, :] = (
        np.array([list(row) for row in fields], dtype=np.float32).astype(np.float64).T
    )
    transform[:3, 3] *= unit
    # A double beyond float32's range reads as infinity; the XML reader keeps it.
    if (
        not np.isfinite(transform).all()
        or float(np.abs(transform[:3, 3]).max()) > SINGLE_PRECISION_REACH_MM
    ):
        raise NativeUnavailable("placement beyond single precision")
    return transform


def read_scene(path: Path, *, max_faces: int) -> ExpandedScene:
    """The package's scene, or ``NativeUnavailable`` for the XML reader."""
    if _BINDING is None:
        raise NativeUnavailable("lib3mf unavailable")
    # The generated binding declares every handle optional; a failed call
    # raises instead of returning None, so its objects are used untyped.
    lib3mf, wrapper = _BINDING
    try:
        unit, printable = _preflight(path)
        model: Any = wrapper.CreateModel()
        model.QueryReader("3mf").ReadFromFile(str(path))
        return _scene(lib3mf, wrapper, model, unit, printable, max_faces=max_faces)
    except MemoryError as exc:
        # Out of memory under the worker's ceiling is a valid package too
        # large to read here; the heavier XML reader would fail the same way.
        raise GeometryError("resource_limit") from exc
    except lib3mf.ELib3MFException as exc:
        if "bad_alloc" in str(exc):
            raise GeometryError("resource_limit") from exc
        raise NativeUnavailable("lib3mf refused the package") from exc


def _scene(
    lib3mf: Any,
    wrapper: Any,
    model: Any,
    unit: float,
    printable: tuple[bool, ...],
    *,
    max_faces: int,
) -> ExpandedScene:
    import numpy as np

    def key(obj: Any) -> str:
        return f"{obj.PackagePart().GetPath().lstrip('/')}#{obj.GetModelResourceID()}"

    objects: dict[str, Any] = {}
    iterator = model.GetObjects()
    while iterator.MoveNext():
        obj = iterator.GetCurrentObject()
        objects[key(obj)] = obj
    if len(objects) > MAX_RESOURCES:
        raise NativeUnavailable("scene resource limit")

    items = model.GetBuildItems()
    build_items = []
    while items.MoveNext():
        build_items.append(items.GetCurrent())
    # lib3mf keeps the root build in document order; the scan saw the same items.
    if len(build_items) != len(printable):
        raise NativeUnavailable("build items differ from the root build")
    build_items = [
        item for item, keep in zip(build_items, printable, strict=True) if keep
    ]
    if len(build_items) > MAX_BUILD_ITEMS:
        raise NativeUnavailable("scene resource limit")
    build = tuple(
        Instance(
            key(item.GetObjectResource()),
            _transform(item.GetObjectTransform().Fields, unit)
            if item.HasObjectTransform()
            else np.eye(4),
        )
        for item in build_items
    )

    pending = [instance.resource_id for instance in reversed(build)]
    admitted: dict[str, MeshResource | Assembly] = {}
    total_faces = total_vertices = 0
    position = ctypes.POINTER(lib3mf.Position)
    triangle = ctypes.POINTER(lib3mf.Triangle)
    while pending:
        resource_id = pending.pop()
        if resource_id in admitted:
            continue
        obj = objects.get(resource_id)
        if obj is None:
            raise NativeUnavailable("missing resource")
        unique = obj.GetUniqueResourceID()
        if obj.IsMeshObject():
            mesh = model.GetMeshObjectByID(unique)
            vertex_count, face_count = mesh.GetVertexCount(), mesh.GetTriangleCount()
            total_vertices += vertex_count
            total_faces += face_count
            if total_faces > max_faces or total_vertices > MAX_ANALYSIS_VERTICES:
                raise GeometryError("resource_limit")
            # The binding's list getters build one Python object per vertex;
            # the C entry points fill NumPy buffers of the same packed layout.
            vertices = np.empty((vertex_count, 3), dtype=np.float32)
            faces = np.empty((face_count, 3), dtype=np.uint32)
            needed = ctypes.c_uint64()
            wrapper.checkError(
                mesh,
                wrapper.lib.lib3mf_meshobject_getvertices(
                    mesh._handle,
                    vertex_count,
                    needed,
                    vertices.ctypes.data_as(position),
                ),
            )
            if needed.value != vertex_count:
                raise NativeUnavailable("vertex count changed")
            wrapper.checkError(
                mesh,
                wrapper.lib.lib3mf_meshobject_gettriangleindices(
                    mesh._handle, face_count, needed, faces.ctypes.data_as(triangle)
                ),
            )
            if needed.value != face_count:
                raise NativeUnavailable("face count changed")
            scaled = vertices.astype(np.float64) * unit
            if scaled.size and not (
                np.isfinite(scaled).all()
                and float(np.abs(scaled).max()) <= SINGLE_PRECISION_REACH_MM
            ):
                raise NativeUnavailable("geometry beyond single precision")
            admitted[resource_id] = MeshResource(
                resource_id, scaled, faces.astype(np.int64)
            )
        elif obj.IsComponentsObject():
            components = model.GetComponentsObjectByID(unique)
            count = components.GetComponentCount()
            if count + len(pending) > MAX_RESOURCES:
                raise NativeUnavailable("scene resource limit")
            children = []
            for index in range(count):
                component = components.GetComponent(index)
                children.append(
                    Instance(
                        key(component.GetObjectResource()),
                        _transform(component.GetTransform().Fields, unit)
                        if component.HasTransform()
                        else np.eye(4),
                    )
                )
            assembly = Assembly(resource_id, tuple(children))
            admitted[resource_id] = assembly
            pending.extend(child.resource_id for child in reversed(assembly.children))
        else:
            # Level sets and other non-mesh objects have no XML-reader meaning.
            raise NativeUnavailable("unsupported object kind")
    return expand_scene(tuple(admitted.values()), build, max_faces=max_faces)
