"""The 3MF source reader owns unique arrays before any consumer materializes them."""

import io
import json
import subprocess
import sys
import zipfile

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.similarity import GeometryError, components

from app.modules.media import mesh_resources, three_mf_scene
from app.modules.media.three_mf_scene import Unsupported3MFCapability, read_scene
from tests.factories.content import zip_bytes
from tests.factories.geometry import tetrahedron, three_mf
from tests.paths import BACKEND_DIR


class TestReadScene:
    def test_preserves_resource_arrays(self, tmp_path):
        path = tmp_path / "part.3mf"
        path.write_bytes(three_mf())

        scene = read_scene(path)

        assert len(scene.resources) == len(scene.instances) == 1
        resource = scene.resources[0]
        assert resource.resource_id == "3D/3dmodel.model#1"
        np.testing.assert_array_equal(
            resource.vertices, [[0, 0, 0], [10, 0, 0], [1, 20, 0], [2, 3, 30]]
        )
        np.testing.assert_array_equal(
            resource.faces, [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
        )
        np.testing.assert_array_equal(scene.instances[0].transform, np.eye(4))
        assert resource.vertices.dtype == np.float64
        assert resource.faces.dtype == np.int64
        assert resource.vertices.flags.owndata
        assert resource.faces.flags.owndata
        prepared = mesh_resources.load_3mf(path)
        assert prepared.whole_resource_id == resource.resource_id

    @pytest.mark.parametrize(
        "unit, scale",
        [
            ("micron", 0.001),
            ("millimeter", 1.0),
            ("centimeter", 10.0),
            ("inch", 25.4),
            ("foot", 304.8),
            ("meter", 1000.0),
        ],
    )
    def test_preserves_supported_physical_units(self, tmp_path, unit, scale):
        path = tmp_path / "units.3mf"
        path.write_bytes(three_mf(unit=unit))
        expected = np.array([[0, 0, 0], [10, 0, 0], [1, 20, 0], [2, 3, 30]]) * scale

        scene = read_scene(path)

        np.testing.assert_array_equal(scene.resources[0].vertices, expected)

    def test_materializes_nested_mirror_in_physical_units(self, tmp_path):
        path = tmp_path / "reflected.3mf"
        path.write_bytes(
            three_mf(
                unit="inch",
                assemblies={2: [(1, "-2 0 0 0 2 0 0 0 2 3 4 5")]},
                build=((2, "1 0 0 0 1 0 0 0 1 100 200 300"),),
            )
        )
        expected_vertices = (
            np.array([[103, 204, 305], [83, 204, 305], [101, 244, 305], [99, 210, 365]])
            * 25.4
        )
        expected_faces = np.array([[1, 2, 0], [3, 1, 0], [2, 3, 0], [3, 2, 1]])

        scene = read_scene(path)
        vertices, faces = components.compose_scene(scene)
        prepared = mesh_resources.load_3mf(path)

        np.testing.assert_allclose(vertices, expected_vertices, rtol=0, atol=1e-12)
        np.testing.assert_array_equal(faces, expected_faces)
        np.testing.assert_array_equal(prepared.whole_mesh.vertices, vertices)
        np.testing.assert_array_equal(prepared.whole_mesh.faces, faces)
        assert prepared.whole_resource_id is None

    def test_retains_unique_geometry_without_materialization(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "plate.3mf"
        path.write_bytes(three_mf(build=((1, None),) * 64))
        monkeypatch.setattr(
            components, "compose_scene", lambda *_: pytest.fail("must not flatten")
        )
        monkeypatch.setattr(
            mesh_resources, "compose_scene", lambda *_: pytest.fail("must not flatten")
        )
        monkeypatch.setattr(
            trimesh, "Trimesh", lambda **_: pytest.fail("must not construct a mesh")
        )

        scene = read_scene(path, max_faces=256)

        assert len(scene.resources) == 1
        assert len(scene.instances) == 64
        resource = scene.resources[0]
        assert resource.vertices.nbytes + resource.faces.nbytes == 192
        assert {instance.resource_id for instance in scene.instances} == {
            resource.resource_id
        }

    def test_owns_arrays_after_archive_closes(self, tmp_path):
        path = tmp_path / "owned.3mf"
        path.write_bytes(three_mf())

        scene = read_scene(path)
        path.unlink()

        vertices, faces = components.compose_scene(scene)
        assert len(vertices) == len(faces) == 4
        np.testing.assert_array_equal(np.ptp(vertices, axis=0), [10, 20, 30])

    def test_preserves_far_resource_precision(self, tmp_path):
        mesh = tetrahedron()
        mesh.vertices += 1e9
        path = tmp_path / "far.3mf"
        path.write_bytes(three_mf(meshes={1: mesh}))

        scene = read_scene(path)

        np.testing.assert_array_equal(scene.resources[0].vertices, mesh.vertices)
        np.testing.assert_array_equal(
            np.ptp(scene.resources[0].vertices, axis=0), [10, 20, 30]
        )

    def test_preserves_expansion_budget_refusal(self, tmp_path, monkeypatch):
        path = tmp_path / "over-budget.3mf"
        path.write_bytes(three_mf(build=((1, None),) * 3))
        monkeypatch.setattr(
            components, "compose_scene", lambda *_: pytest.fail("must not flatten")
        )

        with pytest.raises(GeometryError, match="scene_resource_limit"):
            read_scene(path, max_faces=8)

    def test_reads_without_native_mesh_import(self, tmp_path):
        path = tmp_path / "native-free.3mf"
        path.write_bytes(three_mf())
        source = """
import io
import json
import zipfile
import sys
from pathlib import Path
from app.modules.media.three_mf_scene import Unsupported3MFCapability, read_scene
scene = read_scene(Path(sys.argv[1]))
print(json.dumps({
    "resource_bytes": sum(resource.vertices.nbytes + resource.faces.nbytes for resource in scene.resources),
    "forbidden": [name for name in sys.modules if name == "trimesh" or name.startswith("trimesh.") or name in {
        "app.modules.media.mesh_resources", "app.modules.media.thumbnail_engine", "app.modules.media.geometry_analysis"
    }],
}))
"""

        completed = subprocess.run(
            [sys.executable, "-c", source, str(path)],
            capture_output=True,
            text=True,
            check=True,
            cwd=BACKEND_DIR,
        )

        assert json.loads(completed.stdout) == {
            "resource_bytes": 192,
            "forbidden": [],
        }


def _model_with_attributes(attributes: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(three_mf())) as archive:
        model = archive.read("3D/3dmodel.model")
    return model.replace(b"<model ", b"<model " + attributes.encode() + b" ", 1)


class TestRequiredCapabilities:
    @pytest.mark.parametrize(
        "attributes",
        [
            'xmlns:q="urn:printstash:unknown" requiredextensions="q"',
            'xmlns:p="urn:printstash:unknown" requiredextensions="p"',
            'xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02" requiredextensions="m"',
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" xmlns:q="urn:printstash:unknown" requiredextensions="p q"',
        ],
    )
    def test_refuses_unknown_required_namespace(self, tmp_path, attributes):
        path = tmp_path / "required.3mf"
        path.write_bytes(
            zip_bytes({"3D/3dmodel.model": _model_with_attributes(attributes)})
        )

        with pytest.raises(
            Unsupported3MFCapability, match="unsupported_3mf_capability"
        ) as error:
            read_scene(path)
        assert error.value.namespace in {
            "urn:printstash:unknown",
            "http://schemas.microsoft.com/3dmanufacturing/material/2015/02",
        }

    def test_refuses_an_undeclared_required_prefix(self, tmp_path):
        path = tmp_path / "undeclared.3mf"
        path.write_bytes(
            zip_bytes(
                {"3D/3dmodel.model": _model_with_attributes('requiredextensions="q"')}
            )
        )

        with pytest.raises(GeometryError, match="invalid_required_extension"):
            read_scene(path)

    @pytest.mark.parametrize(
        "attributes",
        [
            'xmlns:q="urn:printstash:unknown"',
            'xmlns:alternate="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="alternate"',
        ],
    )
    def test_accepts_compatible_namespace_metadata(
        self, tmp_path, attributes
    ):
        path = tmp_path / "supported.3mf"
        path.write_bytes(
            zip_bytes({"3D/3dmodel.model": _model_with_attributes(attributes)})
        )

        scene = read_scene(path)

        assert len(scene.resources) == len(scene.instances) == 1
        np.testing.assert_array_equal(
            np.ptp(scene.resources[0].vertices, axis=0), [10, 20, 30]
        )


class TestReachedResources:
    @pytest.mark.parametrize(
        "unused",
        [
            b"broken<xml",
            _model_with_attributes(
                'xmlns:q="urn:printstash:unknown" requiredextensions="q"'
            ),
        ],
    )
    def test_ignores_unreachable_model_parts(self, tmp_path, unused):
        path = tmp_path / "unused-part.3mf"
        path.write_bytes(three_mf(extras={"3D/unused.model": unused}))

        scene = read_scene(path)

        assert len(scene.resources) == len(scene.instances) == 1
        assert scene.resources[0].resource_id == "3D/3dmodel.model#1"
        np.testing.assert_array_equal(
            np.ptp(scene.resources[0].vertices, axis=0), [10, 20, 30]
        )

    def test_does_not_allocate_arrays_for_unreachable_objects(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "unused-resource.3mf"
        path.write_bytes(three_mf(meshes={1: tetrahedron(), 2: tetrahedron()}))
        converted = []
        original = three_mf_scene._attribute_columns

        def capture(mesh, *args):
            converted.append(mesh.getparent().get("id"))
            return original(mesh, *args)

        monkeypatch.setattr(three_mf_scene, "_attribute_columns", capture)

        scene = read_scene(path)

        assert converted == ["1", "1"]
        assert len(scene.resources) == 1
        assert (
            scene.resources[0].vertices.nbytes + scene.resources[0].faces.nbytes == 192
        )


class TestReachedPartValidation:
    @pytest.mark.parametrize(
        "child, error",
        [
            (b"broken<xml", "invalid_3mf"),
            (
                _model_with_attributes(
                    'xmlns:q="urn:printstash:unknown" requiredextensions="q"'
                ),
                "unsupported_3mf_capability",
            ),
            (
                _model_with_attributes('requiredextensions="missing"'),
                "invalid_required_extension",
            ),
        ],
    )
    def test_validates_a_referenced_external_part(self, tmp_path, child, error):
        path = tmp_path / "referenced.3mf"
        path.write_bytes(
            three_mf(
                meshes={},
                assemblies={2: [(1, None)]},
                build=((2, None),),
                external_paths={1: "/3D/child.model"},
                extras={"3D/child.model": child},
            )
        )

        with pytest.raises(GeometryError, match=error):
            read_scene(path)

    def test_ignores_unused_source_faces_under_the_caller_budget(self, tmp_path):
        path = tmp_path / "unique-budget.3mf"
        path.write_bytes(three_mf(meshes={1: tetrahedron(), 2: tetrahedron()}))

        scene = read_scene(path, max_faces=4)

        assert len(scene.resources) == len(scene.instances) == 1
        assert len(scene.resources[0].faces) == 4

    def test_refuses_reached_geometry_before_array_allocation(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "before-allocation.3mf"
        path.write_bytes(three_mf())
        monkeypatch.setattr(
            three_mf_scene,
            "_attribute_columns",
            lambda *_: pytest.fail("must admit source before allocating arrays"),
        )

        with pytest.raises(GeometryError, match="resource_limit"):
            read_scene(path, max_faces=3)

    def test_preserves_declared_main_over_malformed_unreachable_part(self, tmp_path):
        path = tmp_path / "declared.3mf"
        path.write_bytes(
            three_mf(
                model_part="3D/z-build.model",
                relationship_target="/3D/z-build.model",
                extras={
                    "3D/3dmodel.model": b"broken<xml",
                    "3D/a-unused.model": b"broken<xml",
                },
            )
        )

        scene = read_scene(path)

        assert scene.resources[0].resource_id == "3D/z-build.model#1"
        assert len(scene.resources[0].faces) == 4
