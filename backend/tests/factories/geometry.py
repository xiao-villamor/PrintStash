"""Small synthetic geometry with known topology and source transforms."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from tests.factories.content import zip_bytes

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def tetrahedron():
    import numpy as np
    import trimesh

    return trimesh.Trimesh(
        vertices=np.array([[0.0, 0, 0], [10, 0, 0], [1, 20, 0], [2, 3, 30]]),
        faces=np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]),
        process=False,
    )


def three_mf(
    *,
    meshes: dict[int, object] | None = None,
    assemblies: dict[int, list[tuple[int, str | None]]] | None = None,
    build: tuple[tuple[int, str | None], ...] = ((1, None),),
    unit: str = "millimeter",
    extras: dict[str, bytes] | None = None,
    external_paths: dict[int, str] | None = None,
    model_part: str = "3D/3dmodel.model",
    relationship_target: str | None = None,
) -> bytes:
    root = ET.Element("model", xmlns=CORE_NS, unit=unit)
    resources = ET.SubElement(root, "resources")
    for key, mesh in (meshes if meshes is not None else {1: tetrahedron()}).items():
        obj = ET.SubElement(resources, "object", id=str(key), type="model")
        element = ET.SubElement(obj, "mesh")
        vertices = ET.SubElement(element, "vertices")
        for point in mesh.vertices:
            ET.SubElement(
                vertices,
                "vertex",
                **dict(zip(("x", "y", "z"), map(str, point), strict=True)),
            )
        triangles = ET.SubElement(element, "triangles")
        for triangle in mesh.faces:
            ET.SubElement(
                triangles,
                "triangle",
                **dict(zip(("v1", "v2", "v3"), map(str, triangle), strict=True)),
            )
    for key, children in (assemblies or {}).items():
        obj = ET.SubElement(resources, "object", id=str(key), type="model")
        components = ET.SubElement(obj, "components")
        for child, transform in children:
            attributes = {"objectid": str(child)}
            if transform is not None:
                attributes["transform"] = transform
            if external_paths and child in external_paths:
                attributes[
                    "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"
                ] = external_paths[child]
            ET.SubElement(components, "component", **attributes)
    items = ET.SubElement(root, "build")
    for key, transform in build:
        attributes = {"objectid": str(key)}
        if transform is not None:
            attributes["transform"] = transform
        ET.SubElement(items, "item", **attributes)
    entries = {model_part: ET.tostring(root), **(extras or {})}
    if relationship_target is not None:
        relationships = ET.Element(
            "Relationships",
            xmlns="http://schemas.openxmlformats.org/package/2006/relationships",
        )
        ET.SubElement(
            relationships,
            "Relationship",
            Id="model",
            Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel",
            Target=relationship_target,
        )
        entries["_rels/.rels"] = ET.tostring(relationships)
    return zip_bytes(entries)
