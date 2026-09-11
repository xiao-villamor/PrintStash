"""Single-load mesh preparation that retains 3MF resource identity and instances."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

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
    compose_scene,
    expand_scene,
    split_components,
)

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


@dataclass(frozen=True)
class PreparedMesh:
    whole_mesh: Any
    scene: ExpandedScene
    format: str
    complete: bool = True
    failure_code: str | None = None
    brep: dict[str, Any] | None = None
    whole_resource_id: str | None = None


def prepare_loaded_mesh(mesh: Any, *, file_type: str) -> PreparedMesh:
    import numpy as np

    resources = split_components(np.asarray(mesh.vertices), np.asarray(mesh.faces))
    scene = ExpandedScene(
        resources,
        tuple(Instance(resource.resource_id, np.eye(4)) for resource in resources),
    )
    return PreparedMesh(
        mesh,
        scene,
        file_type,
        brep=mesh.metadata.get("brep"),
        whole_resource_id=resources[0].resource_id if len(resources) == 1 else None,
    )


def load_3mf(path: Path, *, max_faces: int = MAX_ANALYSIS_FACES) -> PreparedMesh:
    """Parse bounded XML resources once; never flatten away resource placement.

    Production-extension external .model parts inside the same archive are
    supported. Package paths stay inside the ZIP namespace and never resolve
    against the filesystem or network. DTDs/entities are refused explicitly.
    """
    import numpy as np
    import trimesh
    from lxml import etree

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
            objects: list[MeshResource | Assembly] = []
            build: list[Instance] = []
            total_faces = total_vertices = total_xml = 0
            for name in model_names:
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
                parser = etree.XMLParser(
                    resolve_entities=False,
                    no_network=True,
                    load_dtd=False,
                    huge_tree=False,
                )
                tree = etree.parse(io.BytesIO(payload), parser)
                if tree.docinfo.doctype:
                    raise GeometryError("xml_doctype_forbidden")
                root = tree.getroot()
                if root.tag != f"{{{CORE_NS}}}model":
                    raise GeometryError("invalid_3mf_model")
                unit = UNITS.get(root.get("unit", "millimeter"))
                if unit is None:
                    raise GeometryError("unsupported_unit")
                resources = root.find(f"{{{CORE_NS}}}resources")
                if resources is not None:
                    for obj in resources.findall(f"{{{CORE_NS}}}object"):
                        resource_id = f"{name}#{_object_id(obj.get('id'))}"
                        mesh = obj.find(f"{{{CORE_NS}}}mesh")
                        if mesh is not None:
                            nodes = mesh.findall(
                                f"{{{CORE_NS}}}vertices/{{{CORE_NS}}}vertex"
                            )
                            triangles = mesh.findall(
                                f"{{{CORE_NS}}}triangles/{{{CORE_NS}}}triangle"
                            )
                            total_vertices += len(nodes)
                            total_faces += len(triangles)
                            if (
                                total_faces > max_faces
                                or total_vertices > MAX_ANALYSIS_VERTICES
                            ):
                                raise GeometryError("resource_limit")
                            vertices = (
                                np.array(
                                    [
                                        [
                                            float(node.attrib[key])
                                            for key in ("x", "y", "z")
                                        ]
                                        for node in nodes
                                    ],
                                    dtype=np.float64,
                                ).reshape((-1, 3))
                                * unit
                            )
                            faces = np.array(
                                [
                                    [int(tri.attrib[key]) for key in ("v1", "v2", "v3")]
                                    for tri in triangles
                                ],
                                dtype=np.int64,
                            ).reshape((-1, 3))
                            objects.append(MeshResource(resource_id, vertices, faces))
                        else:
                            children = obj.findall(
                                f"{{{CORE_NS}}}components/{{{CORE_NS}}}component"
                            )
                            objects.append(
                                Assembly(
                                    resource_id,
                                    tuple(
                                        _instance(child, name, unit, names)
                                        for child in children
                                    ),
                                )
                            )
                        if len(objects) > 4096:
                            raise GeometryError("scene_resource_limit")
                if name == main:
                    build = [
                        _instance(item, name, unit, names)
                        for item in root.findall(
                            f"{{{CORE_NS}}}build/{{{CORE_NS}}}item"
                        )
                        if item.get("printable", "1") in ("1", "true")
                    ]
                del root, tree, payload
            scene = expand_scene(tuple(objects), tuple(build), max_faces=max_faces)
            vertices, faces = compose_scene(scene)
            if not np.isfinite(vertices).all():
                raise GeometryError("nonfinite_geometry")
            mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
            same_resource = len(scene.resources) == len(
                scene.instances
            ) == 1 and np.array_equal(scene.instances[0].transform, np.eye(4))
            return PreparedMesh(
                mesh,
                scene,
                "3mf",
                whole_resource_id=(
                    scene.resources[0].resource_id if same_resource else None
                ),
            )
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
    element: Any, document: str, unit: float, names: dict[str, Any]
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
