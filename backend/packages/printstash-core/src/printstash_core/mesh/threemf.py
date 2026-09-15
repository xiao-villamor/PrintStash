"""Load 3MF mesh arrays incrementally while preserving build instances.

Rust inflates stored, DEFLATE, bzip2 and LZMA members into its XML parser.
Only scene XML and numeric arrays survive; unrelated members are not inflated.
"""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict, deque
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from .native_rasterizer import kernel


def _part_path(current: str, reference: str) -> str:
    if "\\" in reference or ":" in reference or "?" in reference or "#" in reference:
        raise ValueError("invalid 3MF component path")
    joined = (
        reference.lstrip("/")
        if reference.startswith("/")
        else posixpath.join(posixpath.dirname(current), reference)
    )
    normalized = posixpath.normpath(joined)
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError("3MF component path escapes the package")
    return normalized


def _transform(attributes: dict[str, str]) -> Any:
    import numpy as np

    matrix = np.eye(4, dtype=np.float64)
    if "transform" in attributes:
        values = np.array(attributes["transform"].split(), dtype=np.float64)
        if values.size != 12 or not np.isfinite(values).all():
            raise ValueError("invalid 3MF transform")
        matrix[:3, :4] = values.reshape((4, 3)).T
    return matrix


def load_scene(path: Path, *, max_bytes: int = 512 * 1024**2) -> Any:
    """Return a complete scene using the required native parser."""
    import networkx as nx
    import numpy as np
    import trimesh

    native = kernel()

    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("invalid 3MF byte limit")

    with ExitStack() as stack:
        archive = stack.enter_context(zipfile.ZipFile(path))
        entries = archive.infolist()
        if len(entries) > 4096 or sum(e.file_size for e in entries) > max_bytes:
            raise ValueError("3MF package limit exceeded")
        names = {e.filename: e for e in entries}
        if len(names) != len(entries):
            raise ValueError("duplicate 3MF package member")
        primary = next((n for n in names if n.lower() == "3d/3dmodel.model"), None)
        if primary is None:
            raise ValueError("3MF model part is missing")
        native_archive = native.ThreeMfArchive(path)
        stack.callback(native_archive.close)
        parts: dict[str, dict[str, ET.Element]] = {}
        arrays: dict[tuple[str, str], list[tuple[Any, Any]]] = {}
        roots: dict[str, ET.Element] = {}
        remaining = max_bytes

        def read_part(part: str) -> None:
            nonlocal remaining
            if part in parts:
                return
            if part not in names:
                raise ValueError("3MF component part is missing")
            info = names[part]
            shell, packed = native_archive.read_part(
                part, info.file_size, info.CRC, remaining
            )
            remaining -= names[part].file_size
            root = ET.fromstring(shell)
            if root.tag.rsplit("}", 1)[-1] != "model":
                raise ValueError("invalid 3MF model root")
            objects = root.findall("./{*}resources/{*}object")
            table = {}
            parsed = iter(packed)
            for obj in objects:
                oid = obj.attrib["id"]
                if oid in table:
                    raise ValueError("duplicate 3MF object id")
                table[oid] = obj
                geometry = []
                for _ in obj.findall(".//{*}mesh"):
                    vertices, faces = next(parsed)
                    geometry.append(
                        (
                            np.frombuffer(vertices, dtype=np.float64).reshape(-1, 3),
                            np.frombuffer(faces, dtype=np.int64).reshape(-1, 3),
                        )
                    )
                if geometry:
                    arrays[(part, oid)] = geometry
            if next(parsed, None) is not None:
                raise ValueError("3MF mesh outside object resources")
            parts[part] = table
            roots[part] = root
            if sum(len(table) for table in parts.values()) > 4096:
                raise ValueError("3MF object limit exceeded")

        read_part(primary)
        unit = roots[primary].get("unit", "millimeters")
        graph = nx.MultiDiGraph()
        world = ("", "world")
        graph.add_node(world)
        build = roots[primary].find("./{*}build")
        if build is None:
            raise ValueError("3MF build is missing")
        for item in build.findall("./{*}item"):
            graph.add_edge(
                world,
                (primary, item.attrib["objectid"]),
                key=item.get("partnumber"),
                matrix=_transform(item.attrib),
            )
        expanded = set()
        pending = deque(graph[world])
        while pending:
            part, oid = pending.popleft()
            node = (part, oid)
            if node in expanded:
                continue
            expanded.add(node)
            read_part(part)
            obj = parts[part].get(oid)
            if obj is None:
                raise ValueError("3MF object reference is missing")
            for component in obj.findall("./{*}components/{*}component"):
                reference = next(
                    (
                        v
                        for k, v in component.attrib.items()
                        if k.rsplit("}", 1)[-1] == "path"
                    ),
                    None,
                )
                target = (
                    _part_path(part, reference) if reference else part,
                    component.attrib["objectid"],
                )
                graph.add_edge(node, target, matrix=_transform(component.attrib))
                pending.append(target)
        if not nx.is_directed_acyclic_graph(graph):
            raise ValueError("cyclic 3MF component graph")
        # Count expanded instances before enumerating paths or copying meshes.
        # A small XML DAG can otherwise describe exponentially many instances.
        visits: dict[Any, int] = defaultdict(int)
        visits[world] = 1
        instance_count = expanded_bytes = traversal_steps = 0
        for node in nx.topological_sort(graph):
            count = visits[node]
            traversal_steps += count
            if traversal_steps > 100_000:
                raise ValueError("3MF instance traversal limit exceeded")
            for child in graph[node]:
                visits[child] += count * len(graph[node][child])
            if graph.out_degree(node) == 0:
                instance_count += count
                expanded_bytes += count * sum(
                    v.nbytes + f.nbytes for v, f in arrays.get(node, [])
                )
            if instance_count > 10_000 or expanded_bytes > max_bytes:
                raise ValueError("3MF expanded geometry limit exceeded")
        # Match the existing loader's traversal and matrix multiplication order.
        # Each path is one instance, including repeated geometry references.
        scene = trimesh.Scene(base_frame="world")
        paths = trimesh.graph.multigraph_paths(graph, world, cutoff=traversal_steps + 1)
        if len(paths) != instance_count:
            raise ValueError("incomplete 3MF instance traversal")
        for index, traversal in enumerate(paths):
            node = traversal[-1][0]
            geometry = arrays.get(node)
            if not geometry:
                raise ValueError("3MF build instance has no mesh")
            key = f"geometry-{node[0]}-{node[1]}"
            if key not in scene.geometry:
                vertices, faces = trimesh.util.append_faces(
                    [g[0] for g in geometry], [g[1] for g in geometry]
                )
                scene.geometry[key] = trimesh.Trimesh(
                    vertices=vertices,
                    faces=faces,
                    metadata={"units": unit},
                    process=False,
                )
            transforms = trimesh.graph.multigraph_collect(graph, traversal, "matrix")
            matrix = (
                transforms[0]
                if len(transforms) == 1
                else trimesh.util.multi_dot(transforms)
            )
            scene.graph.update(
                frame_from="world",
                frame_to=f"instance-{index}",
                matrix=matrix,
                geometry=key,
            )
        return scene
