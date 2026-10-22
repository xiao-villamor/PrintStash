"""lib3mf reads a 3MF scene exactly as the XML reader would, or not at all.

The worker reads every 3MF through this reader first. A scene it returns must
hold the XML reader's resources, placements and units in single precision; a
package it cannot read with those semantics must raise ``NativeUnavailable`` so
the XML reader answers it, with its own refusals and their classification.
"""

import io
import os
import subprocess
import sys
import zipfile

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import three_mf_native
from app.modules.media.three_mf_native import NativeUnavailable, read_scene
from tests.factories.content import zip_bytes
from tests.factories.geometry import tetrahedron, three_mf
from tests.factories.three_mf_pilot import opc_package
from tests.paths import BACKEND_DIR

MATERIALS = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
PART_LINK = (
    b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    b'<Relationship Id="part" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"'
    b' Target="/3D/Objects/part.model"/></Relationships>'
)
FACE_BUDGET = 2_000_000
TETRAHEDRON_VERTICES = [[0, 0, 0], [10, 0, 0], [1, 20, 0], [2, 3, 30]]
TETRAHEDRON_FACES = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]


def _write(tmp_path, payload: bytes):
    path = tmp_path / "package.3mf"
    path.write_bytes(payload)
    return path


def _root(payload: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read("3D/3dmodel.model")


def _with_external_part(part: bytes, *, linked: bool = True) -> bytes:
    """A root that places object 7 from ``3D/Objects/part.model``.

    Producers link every part from the root's relationships; ``linked=False``
    leaves only the production path to it.
    """
    extras = {"3D/Objects/part.model": part}
    if linked:
        extras["3D/_rels/3dmodel.model.rels"] = PART_LINK
    return three_mf(
        meshes={},
        assemblies={2: [(7, None)]},
        build=((2, None),),
        external_paths={7: "/3D/Objects/part.model"},
        extras=extras,
    )


class TestReadScene:
    def test_reads_a_mesh_resource(self, tmp_path):
        scene = read_scene(_write(tmp_path, three_mf()), max_faces=FACE_BUDGET)

        (resource,) = scene.resources
        assert resource.resource_id == "3D/3dmodel.model#1"
        np.testing.assert_array_equal(resource.vertices, TETRAHEDRON_VERTICES)
        np.testing.assert_array_equal(resource.faces, TETRAHEDRON_FACES)

    def test_returns_arrays_in_the_xml_readers_types(self, tmp_path):
        scene = read_scene(_write(tmp_path, three_mf()), max_faces=FACE_BUDGET)

        (resource,) = scene.resources
        assert (resource.vertices.dtype, resource.faces.dtype) == (
            np.float64,
            np.int64,
        )

    def test_rounds_coordinates_to_single_precision(self, tmp_path):
        mesh = tetrahedron()
        mesh.vertices += 0.1

        scene = read_scene(
            _write(tmp_path, three_mf(meshes={1: mesh})), max_faces=FACE_BUDGET
        )

        np.testing.assert_array_equal(
            scene.resources[0].vertices,
            mesh.vertices.astype(np.float32).astype(np.float64),
        )

    @pytest.mark.parametrize(
        ("unit", "scale"),
        [
            ("micron", 0.001),
            ("millimeter", 1.0),
            ("centimeter", 10.0),
            ("inch", 25.4),
            ("foot", 304.8),
            ("meter", 1000.0),
        ],
        ids=lambda value: str(value),
    )
    def test_scales_coordinates_by_the_model_unit(self, tmp_path, unit, scale):
        scene = read_scene(_write(tmp_path, three_mf(unit=unit)), max_faces=FACE_BUDGET)

        np.testing.assert_allclose(
            scene.resources[0].vertices,
            np.array(TETRAHEDRON_VERTICES) * scale,
            rtol=1e-7,
        )

    def test_places_components_through_their_transforms(self, tmp_path):
        payload = three_mf(
            assemblies={2: [(1, "1 0 0 0 1 0 0 0 1 20 0 0")]},
            build=((2, "1 0 0 0 1 0 0 0 1 0 40 0"),),
        )

        scene = read_scene(_write(tmp_path, payload), max_faces=FACE_BUDGET)

        (instance,) = scene.instances
        np.testing.assert_array_equal(instance.transform[:3, 3], [20, 40, 0])

    def test_follows_a_production_path_into_another_part(self, tmp_path):
        part = _root(three_mf(meshes={7: tetrahedron()}, build=()))

        scene = read_scene(
            _write(tmp_path, _with_external_part(part)), max_faces=FACE_BUDGET
        )

        assert [resource.resource_id for resource in scene.resources] == [
            "3D/Objects/part.model#7"
        ]

    def test_extracts_only_reached_objects(self, tmp_path):
        payload = three_mf(meshes={1: tetrahedron(), 2: tetrahedron()})

        scene = read_scene(_write(tmp_path, payload), max_faces=FACE_BUDGET)

        assert [resource.resource_id for resource in scene.resources] == [
            "3D/3dmodel.model#1"
        ]

    def test_leaves_out_build_items_that_are_not_printable(self, tmp_path):
        payload = _root(
            three_mf(build=((1, None), (1, "1 0 0 0 1 0 0 0 1 50 0 0")))
        ).replace(b"<item ", b'<item printable="0" ', 1)

        scene = read_scene(
            _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload}))),
            max_faces=FACE_BUDGET,
        )

        (instance,) = scene.instances
        np.testing.assert_array_equal(instance.transform[:3, 3], [50, 0, 0])

    def test_reads_the_root_named_by_the_package_relationship(self, tmp_path):
        payload = three_mf(
            model_part="3D/root.model", relationship_target="/3D/root.model"
        )

        scene = read_scene(_write(tmp_path, payload), max_faces=FACE_BUDGET)

        assert scene.resources[0].resource_id == "3D/root.model#1"

    def test_leaves_a_package_without_opc_entries_to_the_xml_reader(self, tmp_path):
        with pytest.raises(NativeUnavailable):
            read_scene(_write(tmp_path, three_mf(opc=False)), max_faces=FACE_BUDGET)

    def test_leaves_a_required_extension_to_the_xml_reader(self, tmp_path):
        payload = _root(three_mf()).replace(
            b"<model ",
            f'<model xmlns:m="{MATERIALS}" requiredextensions="m" '.encode(),
            1,
        )

        with pytest.raises(NativeUnavailable):
            read_scene(
                _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload}))),
                max_faces=FACE_BUDGET,
            )

    def test_leaves_a_production_part_without_a_build_to_the_xml_reader(self, tmp_path):
        part = _root(three_mf(meshes={7: tetrahedron()}, build=())).replace(
            b"<build />", b""
        )

        with pytest.raises(NativeUnavailable):
            read_scene(
                _write(tmp_path, _with_external_part(part)), max_faces=FACE_BUDGET
            )

    def test_leaves_a_part_the_root_does_not_link_to_the_xml_reader(self, tmp_path):
        part = _root(three_mf(meshes={7: tetrahedron()}, build=()))

        with pytest.raises(NativeUnavailable):
            read_scene(
                _write(tmp_path, _with_external_part(part, linked=False)),
                max_faces=FACE_BUDGET,
            )

    def test_leaves_parts_in_different_units_to_the_xml_reader(self, tmp_path):
        part = _root(three_mf(meshes={7: tetrahedron()}, build=(), unit="inch"))

        with pytest.raises(NativeUnavailable):
            read_scene(
                _write(tmp_path, _with_external_part(part)), max_faces=FACE_BUDGET
            )

    def test_leaves_a_document_type_declaration_to_the_xml_reader(self, tmp_path):
        payload = b"<!DOCTYPE model>" + _root(three_mf())

        with pytest.raises(NativeUnavailable):
            read_scene(
                _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload}))),
                max_faces=FACE_BUDGET,
            )

    def test_leaves_geometry_beyond_single_precision_to_the_xml_reader(self, tmp_path):
        mesh = tetrahedron()
        mesh.vertices += 1e9

        with pytest.raises(NativeUnavailable):
            read_scene(
                _write(tmp_path, three_mf(meshes={1: mesh})), max_faces=FACE_BUDGET
            )

    def test_leaves_a_placement_beyond_single_precision_to_the_xml_reader(
        self, tmp_path
    ):
        payload = three_mf(build=((1, "1 0 0 0 1 0 0 0 1 1000000000 0 0"),))

        with pytest.raises(NativeUnavailable):
            read_scene(_write(tmp_path, payload), max_faces=FACE_BUDGET)

    def test_leaves_a_transform_beyond_single_precision_range_to_the_xml_reader(
        self, tmp_path
    ):
        payload = three_mf(build=((1, "1e308 0 0 0 1e308 0 0 0 1e308 0 0 0"),))

        with pytest.raises(NativeUnavailable):
            read_scene(_write(tmp_path, payload), max_faces=FACE_BUDGET)

    def test_leaves_more_entries_than_the_xml_reader_accepts_to_it(self, tmp_path):
        entries = {f"Metadata/{index}.txt": b"" for index in range(4096)}
        payload = opc_package(
            zip_bytes({"3D/3dmodel.model": _root(three_mf()), **entries})
        )

        with pytest.raises(NativeUnavailable):
            read_scene(_write(tmp_path, payload), max_faces=FACE_BUDGET)

    def test_leaves_a_package_lib3mf_refuses_to_the_xml_reader(self, tmp_path):
        payload = _root(three_mf()).replace(b'v1="0"', b'v1="99"', 1)

        with pytest.raises(NativeUnavailable):
            read_scene(
                _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload}))),
                max_faces=FACE_BUDGET,
            )

    def test_leaves_every_package_to_the_xml_reader_without_lib3mf(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(three_mf_native, "_BINDING", None)

        with pytest.raises(NativeUnavailable):
            read_scene(_write(tmp_path, three_mf()), max_faces=FACE_BUDGET)

    def test_refuses_faces_over_the_callers_budget(self, tmp_path):
        with pytest.raises(GeometryError, match="resource_limit"):
            read_scene(_write(tmp_path, three_mf()), max_faces=3)

    @pytest.mark.parametrize("headroom_mib", [8, 16], ids=["lib3mf", "arrays"])
    def test_reports_running_out_of_memory_as_a_resource_limit(
        self, tmp_path, headroom_mib
    ):
        # The real fault, induced with a real ceiling above the interpreter's
        # own size, after a small read has loaded what a worker preloads:
        # lib3mf's allocations fail at 8 MiB, the scene's arrays at 16 MiB.
        small = _write(tmp_path, three_mf())
        large = tmp_path / "large.3mf"
        large.write_bytes(
            three_mf(meshes={1: trimesh.creation.icosphere(subdivisions=7)})
        )
        source = """
import resource
import sys
from pathlib import Path
from printstash_core.mesh.similarity import GeometryError
from app.modules.media.three_mf_native import read_scene
read_scene(Path(sys.argv[1]), max_faces=2_000_000)
with open("/proc/self/status") as status:
    size = next(int(line.split()[1]) * 1024 for line in status if line.startswith("VmSize:"))
ceiling = size + int(sys.argv[3]) * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
try:
    read_scene(Path(sys.argv[2]), max_faces=2_000_000)
except GeometryError as exc:
    print(exc.code)
"""

        completed = subprocess.run(
            [sys.executable, "-c", source, str(small), str(large), str(headroom_mib)],
            capture_output=True,
            text=True,
            check=True,
            cwd=BACKEND_DIR,
            env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
        )

        assert completed.stdout.strip() == "resource_limit", completed.stderr
