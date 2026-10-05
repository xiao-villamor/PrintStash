"""Small deterministic visual controls reuse the frozen mesh corpus generators."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from scripts.mesh_corpus_v2_geometry import Mesh, box, combine, torus
from scripts.mesh_corpus_v2_scenes import SceneCase, archive, mesh_xml, model_xml, scene


def _package(mesh: Mesh) -> bytes:
    return archive(
        {"3D/3dmodel.model": model_xml(mesh_xml(mesh), '<item objectid="1"/>').encode()}
    )


def _sphere() -> bytes:
    import trimesh

    mesh = trimesh.creation.icosphere(subdivisions=4)
    return _package(
        Mesh(
            tuple(map(tuple, mesh.vertices.tolist())),
            tuple(map(tuple, mesh.faces.tolist())),
        )
    )


def _grid() -> bytes:
    # The corpus grid family uses two triangles per rectangular cell.
    width, height = 500, 200
    vertices = tuple(
        (float(x), float(y), 0.0) for y in range(height + 1) for x in range(width + 1)
    )
    faces = []
    for y in range(height):
        for x in range(width):
            a = y * (width + 1) + x
            faces.extend(((a, a + 1, a + width + 2), (a, a + width + 2, a + width + 1)))
    return _package(Mesh(vertices, tuple(faces)))


def _benchy() -> bytes:
    source = Path(__file__).resolve().parents[2] / "testdata/benchy/3dbenchy.stl"
    payload = source.read_bytes()
    if (
        hashlib.sha256(payload).hexdigest()
        != "6ab57f1c3f8e86bc3cbd302c6fa6270acf06277c6335454e922419c25d42e97e"
    ):
        raise ValueError("benchy_source_changed")
    return payload


def source_builders() -> dict[str, Callable[[], bytes]]:
    cube = box()
    return {
        "sharp-cube": lambda: _package(cube),
        "hole-torus": lambda: _package(torus()),
        "open-cube": lambda: _package(Mesh(cube.vertices, cube.faces[:-2])),
        "thin-solid": lambda: _package(box(size=(20, 20, 0.125))),
        "remote-component": lambda: _package(
            combine(cube, box(size=(0.125, 0.125, 0.125), offset=(1000, 0, 0)))
        ),
        "reversed-winding": lambda: _package(
            Mesh(cube.vertices, tuple((c, b, a) for a, b, c in cube.faces))
        ),
        "nested-transforms": lambda: scene(SceneCase.NESTED),
        "reflection": lambda: scene(SceneCase.REFLECTION),
        "sheared-placement": lambda: archive(
            {
                "3D/3dmodel.model": model_xml(
                    mesh_xml(cube),
                    '<item objectid="1" transform="1 0 0 0.5 1 0 0 0 1 0 0 0"/>',
                ).encode()
            }
        ),
        "many-instances": lambda: scene(SceneCase.INSTANCES),
        "high-translation": lambda: scene(SceneCase.SOURCE_TRANSLATION),
        "sphere-5120": _sphere,
        "grid-200000": _grid,
        "real-benchy": _benchy,
    }


def write_sources(root: Path, selected: tuple[str, ...] = ()) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    builders = source_builders()
    if set(selected) - builders.keys():
        raise ValueError("unknown_viewer_case")
    paths = {}
    for name in selected or tuple(builders):
        path = root / (name + (".stl" if name == "real-benchy" else ".3mf"))
        with path.open("xb") as output:
            output.write(builders[name]())
        paths[name] = path
    return paths
