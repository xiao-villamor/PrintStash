"""Synthetic source structure and independently computed placements certify target fixtures."""

from __future__ import annotations

import io
import math
import struct
import zipfile
import zlib
from xml.etree import ElementTree

import pytest


def _png_chunks(content: bytes):
    chunks = []
    offset = 8
    while offset < len(content):
        length = struct.unpack_from(">I", content, offset)[0]
        kind = content[offset + 4 : offset + 8]
        payload = content[offset + 8 : offset + 8 + length]
        crc = struct.unpack_from(">I", content, offset + 8 + length)[0]
        chunks.append((kind, payload, crc))
        offset += length + 12
    return chunks


def _matrix(element):
    return tuple(
        map(float, element.attrib.get("transform", "1 0 0 0 1 0 0 0 1 0 0 0").split())
    )


def _apply(points, matrix):
    return tuple(
        (
            tuple(
                (
                    sum((point[row] * matrix[row * 3 + col] for row in range(3)))
                    + matrix[9 + col]
                    for col in range(3)
                )
            )
            for point in points
        )
    )


def _determinant(matrix):
    a, b, c, d, e, f, g, h, i = matrix[:9]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


class TestSceneFixtures:
    @pytest.mark.parametrize(
        ("name", "objects", "items", "triangle_count"),
        [
            ("empty_geometry", 1, 1, 0),
            ("invalid_index", 1, 1, 12),
            ("duplicate_id", 2, 1, 24),
            ("unreferenced_parts", 2, 1, 24),
            ("printable", 1, 2, 12),
        ],
    )
    def test_encodes_core_cases(
        self, corpus_v2, name, objects, items, triangle_count, read_3mf_model
    ) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / f"scene-{name}.3mf")
        assert len(model.findall(".//{*}object")) == objects
        assert len(model.findall(".//{*}item")) == items
        assert len(model.findall(".//{*}triangle")) == triangle_count

    @pytest.mark.parametrize(
        ("name", "objects", "components"),
        [
            ("internal_part", 1, 1),
            ("nested_transforms", 3, 2),
            ("cycle", 3, 2),
            ("exponential_expansion", 22, 42),
            ("reflection", 1, 0),
            ("singular_transform", 1, 0),
            ("unknown_required_extension", 1, 0),
        ],
    )
    def test_encodes_production_cases(
        self, corpus_v2, name, objects, components, read_3mf_model
    ) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / f"scene-{name}.3mf")
        assert len(model.findall(".//{*}object")) == objects
        assert len(model.findall(".//{*}component")) == components

    @pytest.mark.parametrize(
        ("name", "has_preview", "prefix_bytes", "expected_prefixes"),
        [
            ("preview_valid", True, 8, [b"\x89PNG\r\n\x1a\n"]),
            ("preview_broken", True, 9, [b"not a png"]),
            ("preview_absent", False, 8, []),
        ],
    )
    def test_encodes_preview_cases(
        self, corpus_v2, name, has_preview, prefix_bytes, expected_prefixes
    ) -> None:
        root, _ = corpus_v2
        with zipfile.ZipFile(root / f"scene-{name}.3mf") as package:
            assert ("Metadata/thumbnail.png" in package.namelist()) is has_preview
            previews = [
                package.read(n)
                for n in package.namelist()
                if n == "Metadata/thumbnail.png"
            ]
        assert [item[:prefix_bytes] for item in previews] == expected_prefixes

    def test_encodes_root_relationship(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        path = root / "scene-root_relationship.3mf"
        with zipfile.ZipFile(path) as package:
            relationships = ElementTree.fromstring(package.read("_rels/.rels"))
            assert (
                relationships.find("{*}Relationship").attrib["Target"]
                == "/3D/reachable.model"
            )
        reachable = read_3mf_model(path, "3D/reachable.model")
        decoy = read_3mf_model(path)
        assert (
            max((float(v.attrib["x"]) for v in reachable.findall(".//{*}vertex"))) == 20
        )
        assert max((float(v.attrib["x"]) for v in decoy.findall(".//{*}vertex"))) == 999

    def test_retains_printable_flag(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / "scene-printable.3mf")
        items = model.findall(".//{*}item")
        assert items[1].attrib["printable"] == "false"
        assert items[1].attrib["transform"].split()[-3:] == ["1000", "0", "0"]

    def test_encodes_invalid_index(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / "scene-invalid_index.3mf")
        assert model.find(".//{*}triangle").attrib["v3"] == "999"
        assert len(model.findall(".//{*}vertex")) == 8

    def test_encodes_duplicate_ids(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / "scene-duplicate_id.3mf")
        assert [obj.attrib["id"] for obj in model.findall(".//{*}object")] == ["1", "1"]

    def test_encodes_cross_part_reference(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        path = root / "scene-internal_part.3mf"
        model = read_3mf_model(path)
        component = model.find(".//{*}component")
        assert (
            component.attrib[
                "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"
            ]
            == "/3D/parts/part.model"
        )
        assert component.attrib["objectid"] == "1"
        part = read_3mf_model(path, "3D/parts/part.model")
        assert len(part.findall(".//{*}triangle")) == 12

    def test_includes_production_tracking_ids(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        path = root / "scene-internal_part.3mf"
        model = read_3mf_model(path)
        part = read_3mf_model(path, "3D/parts/part.model")
        name = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}UUID"
        nodes = [
            model.find("{*}build"),
            *model.findall(".//{*}item"),
            *model.findall(".//{*}object"),
            *model.findall(".//{*}component"),
            *part.findall(".//{*}object"),
        ]
        ids = [node.attrib[name] for node in nodes]
        assert len(ids) == 5
        assert len(set(ids)) == 5
        assert part.find("{*}build").findall("{*}item") == []

    def test_encodes_nested_translations(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / "scene-nested_transforms.3mf")
        components = model.findall(".//{*}component")
        assert [node.attrib["transform"].split()[-3:] for node in components] == [
            ["10", "0", "0"],
            ["20", "0", "0"],
        ]
        assert model.find(".//{*}item").attrib["transform"].split()[-3:] == [
            "40",
            "0",
            "0",
        ]

    @pytest.mark.parametrize(
        ("name", "linear"),
        [
            ("reflection", [-1, 0, 0, 0, 1, 0, 0, 0, 1]),
            ("singular_transform", [1, 0, 0, 0, 1, 0, 0, 0, 0]),
        ],
    )
    def test_encodes_affine_linear_cases(
        self, corpus_v2, name, linear, read_3mf_model
    ) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / f"scene-{name}.3mf")
        assert (
            list(map(float, model.find(".//{*}item").attrib["transform"].split()[:9]))
            == linear
        )

    def test_encodes_component_cycle(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / "scene-cycle.3mf")
        assert [
            (
                node.attrib["id"],
                node.find("{*}components/{*}component").attrib["objectid"],
            )
            for node in model.findall(".//{*}object")
            if node.find("{*}components") is not None
        ] == [("2", "3"), ("3", "2")]

    def test_encodes_exponential_expansion(self, corpus_v2, read_3mf_model) -> None:
        root, manifest = corpus_v2
        model = read_3mf_model(root / "scene-exponential_expansion.3mf")
        objects = model.findall(".//{*}object")[1:]
        assert all(
            (
                [
                    node.attrib["objectid"]
                    for node in obj.findall("{*}components/{*}component")
                ]
                == [str(int(obj.attrib["id"]) - 1)] * 2
                for obj in objects
            )
        )
        assert 12 * 2 ** len(objects) > 2000000
        entry = next(
            (
                row
                for row in manifest.fixtures
                if row.filename == "scene-exponential_expansion.3mf"
            )
        )
        assert entry.expectation.rule == "expanded_scene_limit"

    def test_encodes_unknown_required_namespace(self, corpus_v2) -> None:
        root, _ = corpus_v2
        path = root / "scene-unknown_required_extension.3mf"
        with zipfile.ZipFile(path) as package:
            content = package.read("3D/3dmodel.model")
        namespaces = dict(
            (
                value
                for _, value in ElementTree.iterparse(
                    io.BytesIO(content), events=("start-ns",)
                )
            )
        )
        model = ElementTree.fromstring(content)
        assert model.attrib["requiredextensions"] == "future"
        assert namespaces["future"] == "https://example.invalid/unimplemented"

    def test_encodes_valid_png_payload(self, corpus_v2) -> None:
        root, _ = corpus_v2
        with zipfile.ZipFile(root / "scene-preview_valid.3mf") as package:
            content = package.read("Metadata/thumbnail.png")
        chunks = _png_chunks(content)
        assert all((crc == zlib.crc32(kind + payload) for kind, payload, crc in chunks))
        compressed = b"".join(
            (payload for kind, payload, _ in chunks if kind == b"IDAT")
        )
        assert zlib.decompress(compressed) == (b"\x00" + b"\xff\xff\xff" * 256) * 192

    def test_encodes_synthetic_plate_metadata(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        path = root / "scene-multiple_plates.3mf"
        with zipfile.ZipFile(path) as package:
            settings = ElementTree.fromstring(
                package.read("Metadata/model_settings.config")
            )
        assert [node.attrib["id"] for node in settings.findall("plate")] == ["1", "2"]
        assert len(read_3mf_model(path).findall(".//{*}item")) == 2

    def test_retains_foreign_metadata(self, corpus_v2, read_3mf_model) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / "scene-foreign_metadata.3mf")
        assert {
            node.attrib["name"]: node.text for node in model.findall("{*}metadata")
        } == {
            "synthetic:slicer-version": "foreign test value",
            "other:ignored": "unrelated metadata",
        }

    @pytest.mark.parametrize(
        ("name", "expected_bbox", "ids"),
        [
            ("nested_transforms", (90, 20, 20), ["3", "1"]),
            ("reflection", (80, 20, 20), ["1", "1"]),
        ],
    )
    def test_transforms_have_analytic_placements(
        self, corpus_v2, name, expected_bbox, ids, read_3mf_model
    ) -> None:
        root, manifest = corpus_v2
        model = read_3mf_model(root / f"scene-{name}.3mf")
        base = model.find(".//{*}object[@id='1']/{*}mesh")
        points = tuple(
            (
                tuple((float(v.attrib[axis]) for axis in ("x", "y", "z")))
                for v in base.findall("{*}vertices/{*}vertex")
            )
        )
        items = model.findall("{*}build/{*}item")
        assert [item.attrib["objectid"] for item in items] == ids
        nested_points = points
        components = model.findall(".//{*}component")
        for component in components:
            nested_points = _apply(nested_points, _matrix(component))
        world_points = _apply(nested_points, _matrix(items[0])) + _apply(
            points, _matrix(items[1])
        )
        measured_bbox = tuple(
            (
                max((p[i] for p in world_points)) - min((p[i] for p in world_points))
                for i in range(3)
            )
        )
        assert measured_bbox == expected_bbox
        scale = abs(
            math.prod((_determinant(_matrix(node)) for node in (*components, items[0])))
        ) + abs(_determinant(_matrix(items[1])))
        assert 8000 * scale == 16000
        count = len(base.findall("{*}triangles/{*}triangle"))
        assert count == 12
        entry = next(
            (row for row in manifest.fixtures if row.filename == f"scene-{name}.3mf")
        )
        assert entry.expectation.bbox_mm == measured_bbox
        assert entry.expectation.triangle_count == count * len(items) == 24
        assert entry.expectation.volume_mm3 == 8000 * scale
        assert entry.instances == 2
        assert entry.source_faces == 12

    def test_resolves_all_zip_part_content_types(self, corpus_v2) -> None:
        root, manifest = corpus_v2
        for entry in (row for row in manifest.fixtures if row.file_type == "3mf"):
            with zipfile.ZipFile(root / entry.filename) as package:
                types = ElementTree.fromstring(package.read("[Content_Types].xml"))
                defaults = {
                    node.attrib["Extension"]: node.attrib["ContentType"]
                    for node in types.findall("{*}Default")
                }
                overrides = {
                    node.attrib["PartName"]: node.attrib["ContentType"]
                    for node in types.findall("{*}Override")
                }
                parts = [
                    info.filename
                    for info in package.infolist()
                    if not info.is_dir() and info.filename != "[Content_Types].xml"
                ]
                for name in parts:
                    assert overrides.get(
                        "/" + name, defaults.get(name.rsplit(".", 1)[-1])
                    ), (entry.filename, name)
