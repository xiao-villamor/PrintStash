"""Streaming 3MF scenes retain all placements and refuse partial geometry."""

from __future__ import annotations

import zipfile

import numpy as np
import pytest

from printstash_core.mesh import threemf

MESH = '<mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/><vertex x="0" y="20" z="0"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh>'
OBJECT = '<object id="1">' + MESH + "</object>"
IDENTITY = "1 0 0 0 1 0 0 0 1 0 0 0"


@pytest.fixture
def package(tmp_path):
    __import__("printstash_mesh_native")

    def write(
        objects=OBJECT,
        build='<item objectid="1"/>',
        unit="millimeter",
        extras=None,
        raw=None,
        compression=zipfile.ZIP_DEFLATED,
    ):
        path = tmp_path / "model.3mf"
        xml = (
            raw
            or f'<model unit="{unit}"><resources>{objects}</resources><build>{build}</build></model>'
        )
        with zipfile.ZipFile(path, "w", compression) as archive:
            archive.writestr("3D/3dmodel.model", xml)
            for name, data in (extras or {}).items():
                archive.writestr(name, data)
        return path

    return write


class TestPartPath:
    @pytest.mark.parametrize(
        "reference,expected",
        [
            ("Objects/part.model", "3D/Objects/part.model"),
            ("/3D/part.model", "3D/part.model"),
        ],
    )
    def test_resolves_package_paths(self, reference, expected):
        assert threemf._part_path("3D/3dmodel.model", reference) == expected

    @pytest.mark.parametrize(
        "reference",
        ["../../outside", "https://example.test/a", "a\\b", "x?query", "x#fragment"],
    )
    def test_refuses_paths_outside_package_namespace(self, reference):
        with pytest.raises(ValueError, match="path"):
            threemf._part_path("3D/3dmodel.model", reference)


class TestLoadScene:
    def test_rejects_outdated_native_extension(self, package, monkeypatch):
        import printstash_mesh_native as native

        monkeypatch.delattr(native, "ThreeMfArchive")
        with pytest.raises(AttributeError, match="ThreeMfArchive"):
            threemf.load_scene(package())

    @pytest.mark.parametrize("compression", [12, 14], ids=["bzip2", "lzma"])
    def test_retains_other_compression_compatibility(self, package, compression):
        scene = threemf.load_scene(package(compression=compression))
        np.testing.assert_array_equal(
            scene.dump()[0].vertices, [[0, 0, 0], [10, 0, 0], [0, 20, 0]]
        )

    @pytest.mark.parametrize(
        "compression", [0, 8, 12, 14], ids=["stored", "deflated", "bzip2", "lzma"]
    )
    def test_imports_without_python_decompression(
        self, package, monkeypatch, compression
    ):
        def unavailable(*args, **kwargs):
            raise AssertionError("Python member decompression is unavailable")

        path = package(compression=compression)
        monkeypatch.setattr(zipfile.ZipFile, "open", unavailable)
        scene = threemf.load_scene(path)
        np.testing.assert_array_equal(
            scene.dump()[0].vertices, [[0, 0, 0], [10, 0, 0], [0, 20, 0]]
        )

    def test_retains_build_instances_with_the_same_part_number(self, package):
        path = package(
            build=(
                '<item objectid="1" partnumber="bracket"/>'
                '<item objectid="1" partnumber="bracket" transform="1 0 0 0 1 0 0 0 1 30 0 0"/>'
            )
        )

        scene = threemf.load_scene(path)

        assert len(scene.graph.nodes_geometry) == 2
        np.testing.assert_array_equal(scene.bounds, [[0, 0, 0], [40, 20, 0]])

    def test_renders_repeated_instances_from_native_resource_handles(
        self, package, monkeypatch
    ):
        import io

        import printstash_mesh_native as native
        import trimesh
        from PIL import Image

        from printstash_core.mesh import native_rasterizer

        path = package(
            build=(
                '<item objectid="1"/>'
                '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 30 0 0"/>'
            )
        )
        scene = threemf.load_scene(path)
        mesh = trimesh.util.concatenate(scene.dump())
        mesh._printstash_native_preview = scene._printstash_native_preview
        monkeypatch.setattr(
            native,
            "render_preview",
            lambda *_a, **_k: pytest.fail("native scene must not copy mesh buffers"),
        )

        image = native_rasterizer.render_preview(mesh, width=80, height=60)

        assert Image.open(io.BytesIO(image)).size == (80, 60)


    @pytest.mark.parametrize(
        "transform",
        [IDENTITY, "-1 0 0 0 1 0 0 0 1 30 40 50", "2 0 0 0 2 0 0 0 2 0 0 0"],
    )
    def test_preserves_transformed_geometry(self, package, transform):
        import trimesh

        path = package(build=f'<item objectid="1" transform="{transform}"/>')
        expected = trimesh.util.concatenate(
            trimesh.load_scene(path, process=False).dump()
        )
        actual = trimesh.util.concatenate(threemf.load_scene(path).dump())
        np.testing.assert_array_equal(actual.vertices, expected.vertices)
        np.testing.assert_array_equal(actual.faces, expected.faces)

    def test_preserves_repeated_nested_instances(self, package):
        import trimesh

        assembly = '<object id="2"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 25 0 0"/><component objectid="1"/></components></object>'
        path = package(
            objects=OBJECT + assembly, build='<item objectid="2"/><item objectid="2"/>'
        )
        expected = trimesh.util.concatenate(
            trimesh.load_scene(path, process=False).dump()
        )
        scene = threemf.load_scene(path)
        actual = trimesh.util.concatenate(scene.dump())
        assert len(scene.graph.nodes_geometry) == 4
        np.testing.assert_array_equal(actual.vertices, expected.vertices)
        np.testing.assert_array_equal(actual.faces, expected.faces)

    def test_preserves_external_component_geometry(self, package):
        obj = '<object id="2"><components><component xmlns:p="urn:production" p:path="/3D/Objects/part.model" objectid="1"/></components></object>'
        external = f"<model><resources>{OBJECT}</resources></model>"
        path = package(
            objects=obj,
            build='<item objectid="2"/>',
            extras={"3D/Objects/part.model": external},
        )
        scene = threemf.load_scene(path)
        np.testing.assert_array_equal(
            scene.dump()[0].vertices, [[0, 0, 0], [10, 0, 0], [0, 20, 0]]
        )

    def test_retains_unit_metadata(self, package):
        scene = threemf.load_scene(package(unit="inch"))
        assert scene.dump()[0].metadata["units"] == "millimeter"
        assert scene.dump()[0].metadata["source_units"] == "inch"

    def test_ignores_unrelated_zip_payloads(self, package):
        scene = threemf.load_scene(
            package(extras={"Metadata/preview.png": b"not-an-image"})
        )
        assert len(scene.dump()[0].faces) == 1

    @pytest.mark.parametrize(
        "objects,build,match",
        [
            (
                '<object id="1"><components><component objectid="1"/></components></object>',
                '<item objectid="1"/>',
                "cyclic",
            ),
            (OBJECT, '<item objectid="2"/>', "reference"),
            (OBJECT + OBJECT, '<item objectid="1"/>', "duplicate"),
            ('<object id="1"/>', '<item objectid="1"/>', "no mesh"),
            (
                '<object id="1"><components><component xmlns:p="urn:p" p:path="/missing.model" objectid="2"/></components></object>',
                '<item objectid="1"/>',
                "part is missing",
            ),
            (
                OBJECT,
                '<item objectid="1" transform="nan 0 0 0 1 0 0 0 1 0 0 0"/>',
                "transform",
            ),
            (OBJECT, '<item objectid="1" transform="0 0 0"/>', "transform"),
        ],
    )
    def test_refuses_invalid_scenes(self, package, objects, build, match):
        with pytest.raises(ValueError, match=match):
            threemf.load_scene(package(objects=objects, build=build))

    @pytest.mark.parametrize("limit", [0, -1, True])
    def test_refuses_invalid_limits(self, package, limit):
        with pytest.raises(ValueError, match="byte limit"):
            threemf.load_scene(package(), max_bytes=limit)

    def test_bounds_package_bytes(self, package):
        with pytest.raises(ValueError, match="package limit"):
            threemf.load_scene(package(), max_bytes=10)

    def test_bounds_expanded_instances(self, package):
        build = '<item objectid="1"/>' * 10001
        with pytest.raises(ValueError, match="expanded geometry limit"):
            threemf.load_scene(package(build=build))

    def test_requires_a_main_model(self, package):
        path = package()
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("unrelated.txt", "nothing")
        with pytest.raises(ValueError, match="model part"):
            threemf.load_scene(path)

    def test_requires_a_model_root(self, package):
        with pytest.raises(ValueError, match="model root"):
            threemf.load_scene(package(raw="<wrong/>"))

    def test_requires_a_build(self, package):
        with pytest.raises(ValueError, match="build is missing"):
            threemf.load_scene(package(raw="<model><resources/></model>"))

    def test_refuses_duplicate_zip_members(self, package):
        path = package()
        with zipfile.ZipFile(path, "a") as archive:
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("3D/3dmodel.model", "<model/>")
        with pytest.raises(ValueError, match="duplicate 3MF package member"):
            threemf.load_scene(path)

    def test_refuses_meshes_outside_resources(self, package):
        with pytest.raises(ValueError, match="mesh outside object resources"):
            threemf.load_scene(package(raw=f"<model>{MESH}<build/></model>"))

    def test_bounds_resource_objects(self, package):
        objects = "".join(f'<object id="{i}"/>' for i in range(4097))
        with pytest.raises(ValueError, match="object limit"):
            threemf.load_scene(package(objects=objects))

    def test_bounds_deep_instance_expansion_before_traversal(self, package):
        objects = OBJECT + "".join(
            f'<object id="{i}"><components><component objectid="{i - 1}"/><component objectid="{i - 1}"/></components></object>'
            for i in range(2, 19)
        )
        with pytest.raises(ValueError, match="traversal limit"):
            threemf.load_scene(package(objects=objects, build='<item objectid="18"/>'))

    def test_refuses_partial_graph_traversal(self, package, monkeypatch):
        import trimesh

        monkeypatch.setattr(trimesh.graph, "multigraph_paths", lambda *a, **k: [])
        with pytest.raises(ValueError, match="incomplete 3MF instance traversal"):
            threemf.load_scene(package())


class TestPhysicalScene:
    def test_converts_inches_to_millimeters(self, package):
        scene = threemf.load_scene(package(unit="inch"))
        np.testing.assert_allclose(scene.to_mesh().extents, [254, 508, 0])

    def test_scales_instance_translation_with_document_units(self, package):
        path = package(
            unit="inch",
            build='<item objectid="1" transform="1 0 0 0 1 0 0 0 1 1 0 0"/>',
        )
        np.testing.assert_allclose(threemf.load_scene(path).bounds[0], [25.4, 0, 0])

    def test_resolves_a_build_reference_to_another_part(self, package):
        path = package(
            objects="",
            build='<item xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" p:path="/3D/part.model" objectid="1"/>',
            extras={"3D/part.model": f"<model><resources>{OBJECT}</resources></model>"},
        )
        assert len(threemf.load_scene(path).to_mesh().faces) == 1

    def test_follows_the_package_root_relationship(self, package):
        path = package(
            extras={
                "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/other.model"/></Relationships>',
                "3D/other.model": f'<model unit="inch"><resources>{OBJECT}</resources><build><item objectid="1"/></build></model>',
            }
        )
        np.testing.assert_allclose(
            threemf.load_scene(path).to_mesh().extents, [254, 508, 0]
        )

    def test_omits_non_printable_build_items(self, package):
        path = package(build='<item objectid="1"/><item objectid="1" printable="0"/>')
        assert len(threemf.load_scene(path).to_mesh().faces) == 1


class TestThreemfContract:
    def test_rejects_unknown_document_units(self, package):
        with pytest.raises(ValueError, match="unsupported 3MF unit"):
            threemf.load_scene(package(unit="parsec"))

    @pytest.mark.parametrize(
        "relationship",
        [
            '<Relationship Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="https://example.com/model" TargetMode="External"/>',
            '<Relationship Type="unrelated" Target="/3D/3dmodel.model"/>',
            '<Relationship Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/>'
            * 2,
        ],
    )
    def test_rejects_ambiguous_or_external_package_roots(self, package, relationship):
        path = package(
            extras={
                "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + relationship
                + "</Relationships>"
            }
        )
        with pytest.raises(ValueError, match="invalid 3MF root relationship"):
            threemf.load_scene(path)

    def test_bounds_compressed_root_relationship_expansion(self, package):
        path = package(
            extras={
                "_rels/.rels": "<Relationships>"
                + " " * (1024 * 1024)
                + "</Relationships>"
            }
        )
        with pytest.raises(ValueError, match="3MF relationship limit exceeded"):
            threemf.load_scene(path)
