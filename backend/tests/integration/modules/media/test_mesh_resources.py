"""3MF resource graphs retain units, nesting and instance counts under strict caps."""

import numpy as np
import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media.mesh_resources import load_3mf
from tests.factories import content
from tests.factories.geometry import three_mf


class TestThreeMFResources:
    def test_preserves_plate_multiplicity(self, tmp_path):
        path = tmp_path / "plate.3mf"
        build = tuple((1, f"1 0 0 0 1 0 0 0 1 {i * 50} 0 0") for i in range(6))
        path.write_bytes(three_mf(build=build))

        prepared = load_3mf(path)

        assert len(prepared.scene.resources) == 1
        assert len(prepared.scene.instances) == 6
        assert len(prepared.whole_mesh.faces) == 24
        assert np.ptp(prepared.whole_mesh.vertices[:, 0]) == pytest.approx(260)

    def test_applies_nested_mirror_in_physical_units(self, tmp_path):
        path = tmp_path / "nested.3mf"
        path.write_bytes(
            three_mf(
                unit="inch",
                assemblies={2: [(1, "-2 0 0 0 2 0 0 0 2 3 4 5")]},
                build=((2, "1 0 0 0 1 0 0 0 1 100 200 300"),),
            )
        )

        prepared = load_3mf(path)

        transform = prepared.scene.instances[0].transform
        np.testing.assert_allclose(transform[:3, 3], np.array([103, 204, 305]) * 25.4)
        assert np.linalg.det(transform[:3, :3]) == pytest.approx(-8)
        assert np.ptp(prepared.whole_mesh.vertices[:, 0]) == pytest.approx(20 * 25.4)

    def test_refuses_cyclic_resource_graph(self, tmp_path):
        path = tmp_path / "cycle.3mf"
        path.write_bytes(
            three_mf(meshes={}, assemblies={1: [(2, None)], 2: [(1, None)]})
        )

        with pytest.raises(GeometryError, match="cyclic_resource"):
            load_3mf(path)

    def test_refuses_dtd_without_reading_external_entity(self, tmp_path):
        secret = tmp_path / "private"
        secret.write_text("must-not-be-read")
        xml = f'<!DOCTYPE model [<!ENTITY x SYSTEM "{secret.as_uri()}">]><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">&x;</model>'.encode()
        path = tmp_path / "entity.3mf"
        path.write_bytes(content.zip_bytes({"3D/3dmodel.model": xml}))

        with pytest.raises(GeometryError, match="xml_doctype_forbidden"):
            load_3mf(path)

    @pytest.mark.parametrize(
        "value", ["0 0 0", "1 0 0 0 0 0 0 0 1 0 0 0", "nan 0 0 0 1 0 0 0 1 0 0 0"]
    )
    def test_refuses_invalid_placement(self, tmp_path, value):
        path = tmp_path / "transform.3mf"
        path.write_bytes(three_mf(build=((1, value),)))

        with pytest.raises(GeometryError, match="transform"):
            load_3mf(path)

    def test_caps_expanded_faces(self, tmp_path):
        path = tmp_path / "copies.3mf"
        path.write_bytes(three_mf(build=((1, None),) * 3))

        with pytest.raises(GeometryError, match="resource_limit"):
            load_3mf(path, max_faces=8)


class TestRootRelationship:
    def test_uses_declared_main_part(self, tmp_path):
        path = tmp_path / "nonstandard.3mf"
        path.write_bytes(
            three_mf(
                model_part="3D/z-build.model",
                relationship_target="/3D/z-build.model",
                extras={
                    "3D/a-unused.model": b'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources/><build/></model>'
                },
            )
        )
        prepared = load_3mf(path)
        assert len(prepared.whole_mesh.faces) == 4
        assert prepared.scene.resources[0].resource_id.startswith("3D/z-build.model#")

    @pytest.mark.parametrize(
        "target,code",
        [
            ("../outside.model", "unsafe_resource_path"),
            ("https://example.test/part.model", "unsafe_resource_path"),
            ("/missing.model", "invalid_3mf_relationship"),
        ],
    )
    def test_refuses_unsafe_root_target(self, tmp_path, target, code):
        path = tmp_path / "invalid.3mf"
        path.write_bytes(three_mf(relationship_target=target))
        with pytest.raises(GeometryError, match=code):
            load_3mf(path)


class TestMalformedPackages:
    @pytest.mark.parametrize("budget", [0, 200001, True])
    def test_rejects_invalid_scene_budget(self, tmp_path, budget):
        with pytest.raises(GeometryError, match="invalid_scene_budget"):
            load_3mf(tmp_path / "absent.3mf", max_faces=budget)

    @pytest.mark.parametrize(
        "payload,code",
        [
            (b"not-a-zip", "invalid_3mf"),
            (content.zip_bytes({"missing.txt": b"x"}), "empty_scene"),
            (content.zip_bytes({"3D/3dmodel.model": b"broken<xml"}), "invalid_3mf"),
            (
                content.zip_bytes({"3D/3dmodel.model": b"<different/>"}),
                "invalid_3mf_model",
            ),
            (three_mf(unit="yard"), "unsupported_unit"),
            (three_mf(build=((0, None),)), "invalid_resource_id"),
        ],
    )
    def test_contains_malformed_package(self, tmp_path, payload, code):
        path = tmp_path / "bad.3mf"
        path.write_bytes(payload)
        with pytest.raises(GeometryError, match=code):
            load_3mf(path)

    def test_refuses_duplicate_archive_entries(self, tmp_path):
        import zipfile

        path = tmp_path / "duplicate.3mf"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("3D/3dmodel.model", b"<model/>")
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("3D/3dmodel.model", b"<model/>")
        with pytest.raises(GeometryError, match="duplicate_archive_entry"):
            load_3mf(path)

    def test_refuses_excessive_entry_count(self, tmp_path):
        path = tmp_path / "entries.3mf"
        path.write_bytes(content.zip_bytes({str(index): b"" for index in range(4097)}))
        with pytest.raises(GeometryError, match="archive_resource_limit"):
            load_3mf(path)

    def test_refuses_xml_compression_bomb(self, tmp_path):
        path = tmp_path / "compressed.3mf"
        path.write_bytes(content.zip_bytes({"3D/3dmodel.model": b" " * 500_000}))
        with pytest.raises(GeometryError, match="archive_resource_limit"):
            load_3mf(path)


class TestProductionResources:
    def test_preserves_cross_document_resource_identity(self, tmp_path):
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(three_mf())) as source:
            child = source.read("3D/3dmodel.model")
        path = tmp_path / "external-part.3mf"
        path.write_bytes(
            three_mf(
                meshes={},
                assemblies={2: [(1, None)]},
                build=((2, None),),
                external_paths={1: "/3D/child.model"},
                extras={"3D/child.model": child},
            )
        )
        result = load_3mf(path)
        assert len(result.whole_mesh.faces) == 4
        assert result.scene.resources[0].resource_id == "3D/child.model#1"

    @pytest.mark.parametrize(
        "target", ["../outside.model", "https://example.test/model", "/3D/absent.model"]
    )
    def test_refuses_unavailable_external_part(self, tmp_path, target):
        path = tmp_path / "invalid-path.3mf"
        path.write_bytes(
            three_mf(
                meshes={},
                assemblies={2: [(1, None)]},
                build=((2, None),),
                external_paths={1: target},
            )
        )
        with pytest.raises(GeometryError, match="invalid_resource_path"):
            load_3mf(path)
