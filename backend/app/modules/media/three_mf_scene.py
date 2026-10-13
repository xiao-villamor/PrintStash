"""Bounded 3MF source reading that owns unique arrays and explicit instances."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, TypeVar, cast

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.budgets import (
    MAX_ANALYSIS_FACES,
    MAX_ANALYSIS_VERTICES,
)
from printstash_core.mesh.similarity.components import (
    Assembly,
    ExpandedScene,
    Instance,
    MeshResource,
    expand_scene,
)

if TYPE_CHECKING:
    import numpy as np
    from lxml.etree import _Element
    from numpy.typing import NDArray

    NumericScalar = TypeVar("NumericScalar", np.float64, np.int64)

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
UNITS = {
    "micron": 0.001,
    "millimeter": 1.0,
    "centimeter": 10.0,
    "inch": 25.4,
    "foot": 304.8,
    "meter": 1000.0,
}


_NAMESPACES = {"c": CORE_NS}


class Unsupported3MFCapability(GeometryError):
    """A reached model requires geometry semantics the reader cannot implement."""

    def __init__(self, namespace: str):
        if not isinstance(namespace, str) or not namespace:
            raise TypeError("invalid_capability_namespace")
        self.namespace = namespace
        super().__init__("unsupported_3mf_capability")


@dataclass(frozen=True)
class _ModelPart:
    root: _Element
    unit: float
    objects: dict[str, _Element]


def _required_capabilities(root: _Element) -> None:
    # Prefix labels are document-local. Only their resolved URI identifies the
    # capability; an unknown namespace called "p" is still unsupported.
    for prefix in root.get("requiredextensions", "").split():
        namespace = root.nsmap.get(prefix)
        if namespace is None:
            raise GeometryError("invalid_required_extension")
        if namespace not in {CORE_NS, PRODUCTION_NS}:
            raise Unsupported3MFCapability(namespace)


def _count(mesh: _Element, path: str) -> int:
    """Child count computed inside libxml2, without an element proxy per child."""
    return int(cast(float, mesh.xpath(f"count({path})", namespaces=_NAMESPACES)))


def _attribute_columns(
    mesh: _Element,
    path: str,
    names: tuple[str, ...],
    dtype: type[NumericScalar],
    count: int,
) -> NDArray[NumericScalar]:
    """Read ``names`` from every ``path`` element as a ``(count, len(names))`` array.

    One XPath per attribute hands NumPy a flat list of strings that it converts in
    C, instead of a Python ``float(node.attrib[key])`` per coordinate. An element
    that lacks one of the attributes makes that column short, which is refused
    here rather than silently shifting every later row.
    """
    import numpy as np

    columns: list[list[str]] = []
    for name in names:
        # Attribute XPath returns a string list; NumPy validates numeric input.
        values = cast(
            list[str],
            mesh.xpath(f"{path}/@{name}", namespaces=_NAMESPACES, smart_strings=False),
        )
        if len(values) != count:
            raise GeometryError("invalid_3mf")
        columns.append(values)
    return np.array(columns, dtype=dtype).T.copy()


def read_scene(path: Path, *, max_faces: int = MAX_ANALYSIS_FACES) -> ExpandedScene:
    """Parse bounded XML resources once; never flatten away resource placement.

    Production-extension external .model parts inside the same archive are
    supported. Package paths stay inside the ZIP namespace and never resolve
    against the filesystem or network. DTDs/entities are refused explicitly.
    """
    import lxml.etree as etree
    import numpy as np

    if type(max_faces) is not int or not 1 <= max_faces <= MAX_ANALYSIS_FACES:
        raise GeometryError("invalid_scene_budget")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 4096
                or sum(entry.file_size for entry in entries) > 512 * 1024 * 1024
            ):
                raise GeometryError("archive_resource_limit")
            names = {entry.filename: entry for entry in entries}
            if len(names) != len(entries):
                raise GeometryError("duplicate_archive_entry")
            model_names = sorted(
                name for name in names if name.lower().endswith(".model")
            )
            if not model_names:
                raise GeometryError("empty_scene")
            main = next(
                (name for name in model_names if name.lower() == "3d/3dmodel.model"),
                model_names[0],
            )
            if "_rels/.rels" in names:
                relationship = names["_rels/.rels"]
                if (
                    relationship.file_size > 1024 * 1024
                    or relationship.file_size > max(relationship.compress_size, 1) * 200
                ):
                    raise GeometryError("archive_resource_limit")
                payload = archive.read(relationship)
                tree = etree.parse(
                    io.BytesIO(payload),
                    etree.XMLParser(
                        resolve_entities=False, no_network=True, load_dtd=False
                    ),
                )
                if tree.docinfo.doctype:
                    raise GeometryError("xml_doctype_forbidden")
                roots = [
                    node
                    for node in tree.getroot()
                    if node.tag
                    == "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
                    and node.get("Type")
                    == "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
                ]
                if (
                    len(roots) != 1
                    or roots[0].get("TargetMode", "Internal") != "Internal"
                ):
                    raise GeometryError("invalid_3mf_relationship")
                target = roots[0].get("Target", "")
                if (
                    "\\" in target
                    or ":" in target
                    or ".." in PurePosixPath(target).parts
                ):
                    raise GeometryError("unsafe_resource_path")
                main = target.lstrip("/")
                if main not in model_names:
                    raise GeometryError("invalid_3mf_relationship")
            parts: dict[str, _ModelPart] = {}
            total_xml = total_indexed = 0

            def read_part(name: str) -> _ModelPart:
                nonlocal total_xml, total_indexed
                if name in parts:
                    return parts[name]
                info = names[name]
                total_xml += info.file_size
                if (
                    total_xml > 64 * 1024 * 1024
                    or info.file_size > max(info.compress_size, 1) * 200
                ):
                    raise GeometryError("archive_resource_limit")
                with archive.open(info) as stream:
                    payload = stream.read(64 * 1024 * 1024 + 1)
                if len(payload) != info.file_size:
                    raise GeometryError("invalid_archive")
                tree = etree.parse(
                    io.BytesIO(payload),
                    etree.XMLParser(
                        resolve_entities=False,
                        no_network=True,
                        load_dtd=False,
                        huge_tree=False,
                    ),
                )
                if tree.docinfo.doctype:
                    raise GeometryError("xml_doctype_forbidden")
                root = tree.getroot()
                if root.tag != f"{{{CORE_NS}}}model":
                    raise GeometryError("invalid_3mf_model")
                _required_capabilities(root)
                unit = UNITS.get(root.get("unit", "millimeter"))
                if unit is None:
                    raise GeometryError("unsupported_unit")
                indexed: dict[str, _Element] = {}
                resources = root.find(f"{{{CORE_NS}}}resources")
                if resources is not None:
                    for obj in resources.findall(f"{{{CORE_NS}}}object"):
                        object_id = _object_id(obj.get("id"))
                        if object_id in indexed:
                            raise GeometryError("duplicate_resource")
                        indexed[object_id] = obj
                        total_indexed += 1
                        if total_indexed > 4096:
                            raise GeometryError("scene_resource_limit")
                part = _ModelPart(root, unit, indexed)
                parts[name] = part
                return part

            main_part = read_part(main)
            build_elements = [
                item
                for item in main_part.root.findall(
                    f"{{{CORE_NS}}}build/{{{CORE_NS}}}item"
                )
                if item.get("printable", "1") in ("1", "true")
            ]
            if len(build_elements) > 2048:
                raise GeometryError("scene_resource_limit")
            build = tuple(
                _instance(item, main, main_part.unit, names) for item in build_elements
            )
            pending = [instance.resource_id for instance in reversed(build)]
            admitted: dict[str, MeshResource | Assembly] = {}
            total_faces = total_vertices = 0
            while pending:
                resource_id = pending.pop()
                if resource_id in admitted:
                    continue
                document, object_id = resource_id.rsplit("#", 1)
                part = read_part(document)
                obj = part.objects.get(object_id)
                if obj is None:
                    raise GeometryError("missing_resource")
                mesh = obj.find(f"{{{CORE_NS}}}mesh")
                if mesh is not None:
                    vertex_count = _count(mesh, "c:vertices/c:vertex")
                    triangle_count = _count(mesh, "c:triangles/c:triangle")
                    total_vertices += vertex_count
                    total_faces += triangle_count
                    if (
                        total_faces > max_faces
                        or total_vertices > MAX_ANALYSIS_VERTICES
                    ):
                        raise GeometryError("resource_limit")
                    vertices = (
                        _attribute_columns(
                            mesh,
                            "c:vertices/c:vertex",
                            ("x", "y", "z"),
                            np.float64,
                            vertex_count,
                        )
                        * part.unit
                    )
                    faces = _attribute_columns(
                        mesh,
                        "c:triangles/c:triangle",
                        ("v1", "v2", "v3"),
                        np.int64,
                        triangle_count,
                    )
                    admitted[resource_id] = MeshResource(resource_id, vertices, faces)
                else:
                    children = obj.findall(
                        f"{{{CORE_NS}}}components/{{{CORE_NS}}}component"
                    )
                    if len(children) + len(pending) > 4096:
                        raise GeometryError("scene_resource_limit")
                    assembly = Assembly(
                        resource_id,
                        tuple(
                            _instance(child, document, part.unit, names)
                            for child in children
                        ),
                    )
                    admitted[resource_id] = assembly
                    pending.extend(
                        child.resource_id for child in reversed(assembly.children)
                    )
            return expand_scene(tuple(admitted.values()), build, max_faces=max_faces)
    except GeometryError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        OverflowError,
        zipfile.BadZipFile,
        etree.XMLSyntaxError,
    ) as exc:
        raise GeometryError("invalid_3mf") from exc


def _object_id(value: str | None) -> str:
    if (
        value is None
        or not value.isascii()
        or not value.isdecimal()
        or not 1 <= int(value) <= 2**31 - 1
    ):
        raise GeometryError("invalid_resource_id")
    return str(int(value))


def _instance(
    element: _Element, document: str, unit: float, names: dict[str, zipfile.ZipInfo]
) -> Instance:
    import numpy as np

    target = document
    external = element.get(f"{{{PRODUCTION_NS}}}path")
    if external is not None:
        target = external.lstrip("/")
        parts = PurePosixPath(target).parts
        if (
            ".." in parts
            or "\\" in target
            or not target.lower().endswith(".model")
            or target not in names
        ):
            raise GeometryError("invalid_resource_path")
    transform = np.eye(4)
    raw = element.get("transform")
    if raw is not None:
        values = [float(value) for value in raw.split()]
        if len(values) != 12:
            raise GeometryError("invalid_transform")
        transform[:3, :] = np.array(values).reshape((4, 3)).T
        transform[:3, 3] *= unit
    return Instance(f"{target}#{_object_id(element.get('objectid'))}", transform)
