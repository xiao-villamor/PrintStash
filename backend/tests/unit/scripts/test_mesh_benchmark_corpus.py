"""Frozen fixtures express geometry contracts independently of parser behavior."""

from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree

import pytest

from scripts.mesh_benchmark_corpus import build_contract_corpus, verify_manifest
from tests.paths import BACKEND_DIR


@pytest.fixture
def corpus(tmp_path: Path):
    manifest = build_contract_corpus(tmp_path)
    return tmp_path, manifest


class TestBuildContractCorpus:
    def test_matches_frozen_manifest(self, corpus) -> None:
        _, manifest = corpus
        expected = json.loads(
            (BACKEND_DIR / "benchmarks/mesh/corpus-v1.json").read_text()
        )
        assert json.loads(json.dumps(asdict(manifest))) == expected

    def test_repeats_identical_fixture_bytes(self, corpus, tmp_path: Path) -> None:
        root, manifest = corpus
        other = tmp_path / "repeat"
        other.mkdir()
        repeated = build_contract_corpus(other)
        assert repeated == manifest
        for entry in manifest.fixtures:
            assert (root / entry.filename).read_bytes() == (
                other / entry.filename
            ).read_bytes()

    def test_hashes_actual_generated_inputs(self, corpus) -> None:
        root, manifest = corpus
        for entry in manifest.fixtures:
            data = (root / entry.filename).read_bytes()
            assert entry.sha256 == hashlib.sha256(data).hexdigest()
            assert entry.input_bytes == len(data)
            assert entry.license == "AGPL-3.0"
            assert entry.origin.startswith("generated:")

    def test_matches_analytic_binary_cube(self, corpus) -> None:
        root, _ = corpus
        data = (root / "cube-binary.stl").read_bytes()
        points = []
        signed_volume = 0.0
        for offset in range(84, len(data), 50):
            values = struct.unpack_from("<12fH", data, offset)
            a, b, c = values[3:6], values[6:9], values[9:12]
            points.extend([a, b, c])
            signed_volume += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                + a[1] * (b[2] * c[0] - b[0] * c[2])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            ) / 6
        assert tuple(
            max(point[axis] for point in points) - min(point[axis] for point in points)
            for axis in range(3)
        ) == (20, 20, 20)
        assert signed_volume == pytest.approx(8000)

    def test_retains_binary_solid_header(self, corpus) -> None:
        root, _ = corpus
        data = (root / "binary-solid.stl").read_bytes()
        assert data.startswith(b"solid")
        assert struct.unpack_from("<I", data, 80)[0] == 12
        assert len(data) == 84 + 12 * 50

    @pytest.mark.parametrize(
        "filename", ["binary-truncated.stl", "binary-count-mismatch.stl"]
    )
    def test_encodes_binary_length_violation(self, corpus, filename: str) -> None:
        root, _ = corpus
        data = (root / filename).read_bytes()
        assert len(data) != 84 + struct.unpack_from("<I", data, 80)[0] * 50

    def test_encodes_ascii_whitespace_variation(self, corpus) -> None:
        root, _ = corpus
        text = (root / "ascii-whitespace.stl").read_text()
        assert text.count("facet normal") == 12
        assert text.count("vertex") == 36
        assert "\t" in text
        assert "e+" in text

    def test_encodes_incomplete_ascii_facet(self, corpus) -> None:
        root, manifest = corpus
        text = (root / "ascii-incomplete.stl").read_text()
        assert text.count("facet normal") == 1
        assert text.count("vertex") == 2
        entry = next(
            entry
            for entry in manifest.fixtures
            if entry.filename == "ascii-incomplete.stl"
        )
        assert entry.source_faces == 0
        assert entry.expectation.rule == "incomplete_ascii_facet"

    @pytest.mark.parametrize(
        ("filename", "unit", "items", "vertices"),
        [
            ("cube-mm.3mf", "millimeter", 1, 8),
            ("cube-inch.3mf", "inch", 1, 8),
            ("cube-micron.3mf", "micron", 1, 8),
            ("multiple-build.3mf", "millimeter", 2, 8),
            ("unused-vertex.3mf", "millimeter", 1, 9),
        ],
    )
    def test_encodes_declared_scene_structure(
        self, corpus, filename, unit, items, vertices
    ) -> None:
        root, _ = corpus
        with zipfile.ZipFile(root / filename) as archive:
            model = ElementTree.fromstring(archive.read("3D/3dmodel.model"))
            assert all(
                info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
            )
        assert model.attrib["unit"] == unit
        assert len(model.findall(".//{*}item")) == items
        assert len(model.findall(".//{*}vertex")) == vertices

    def test_encodes_reflected_build(self, corpus) -> None:
        root, _ = corpus
        with zipfile.ZipFile(root / "reflected-build.3mf") as archive:
            model = ElementTree.fromstring(archive.read("3D/3dmodel.model"))
        assert model.find(".//{*}item").attrib["transform"].split()[0] == "-1"

    def test_encodes_component_cycle(self, corpus) -> None:
        root, manifest = corpus
        with zipfile.ZipFile(root / "cyclic-components.3mf") as archive:
            model = ElementTree.fromstring(archive.read("3D/3dmodel.model"))
        assert (
            model.find(".//{*}object[@id='2']/{*}components/{*}component").attrib[
                "objectid"
            ]
            == "2"
        )
        entry = next(
            entry
            for entry in manifest.fixtures
            if entry.filename == "cyclic-components.3mf"
        )
        assert entry.instances is None
        assert entry.expectation.outcome == "refuse"

    def test_encodes_unsupported_required_extension(self, corpus) -> None:
        root, _ = corpus
        with zipfile.ZipFile(root / "unsupported-required.3mf") as archive:
            model = ElementTree.fromstring(archive.read("3D/3dmodel.model"))
        assert model.attrib["requiredextensions"] == "unknown"

    def test_marks_expectations_as_targets(self, corpus) -> None:
        _, manifest = corpus
        assert manifest.expectation_scope == "target_contract_not_observed_compliance"
        by_name = {entry.filename: entry for entry in manifest.fixtures}
        assert by_name["cube-inch.3mf"].expectation.bbox_mm == (25.4, 25.4, 25.4)
        assert by_name["cube-inch.3mf"].expectation.volume_mm3 == pytest.approx(25.4**3)
        assert by_name["cube-micron.3mf"].expectation.volume_mm3 == 1e-9
        assert by_name["cube-micron.3mf"].expectation.absolute_tolerance == 0
        assert by_name["multiple-build.3mf"].expectation.volume_mm3 == 16000
        assert by_name["unused-vertex.3mf"].expectation.bbox_mm == (20, 20, 20)


class TestVerifyManifest:
    def test_detects_changed_fixture(self, corpus) -> None:
        root, manifest = corpus
        (root / manifest.fixtures[0].filename).write_bytes(b"changed")
        with pytest.raises(ValueError, match="fixture content differs"):
            verify_manifest(root, manifest)
