"""Corpus source facts and analytic expectations are independent of application parsers."""

from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import zipfile
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.mesh_benchmark_corpus import build_contract_corpus as build_v1
from scripts.mesh_benchmark_corpus_v2 import (
    build_contract_corpus,
    recovery_scenarios,
    verify_manifest,
)
from scripts.mesh_corpus_v2_contracts import Family, Profile, RecoveryKind
from scripts.mesh_corpus_v2_geometry import WRITE_CHUNK_BYTES, write_grid
from tests.paths import BACKEND_DIR


def _triangles(path: Path):
    content = path.read_bytes()
    return tuple(
        (
            tuple((tuple(values[i : i + 3]) for i in (3, 6, 9)))
            for offset in range(84, len(content), 50)
            for values in (struct.unpack_from("<12fH", content, offset),)
        )
    )


class TestCorpusV2:
    def test_matches_frozen_manifest(self, corpus_v2) -> None:
        _, manifest = corpus_v2
        expected = json.loads(
            (BACKEND_DIR / "benchmarks/mesh/corpus-v2.json").read_text()
        )
        expected["fixtures"] = [
            row for row in expected["fixtures"] if row["profile"] == "small"
        ]
        assert json.loads(json.dumps(asdict(manifest))) == expected

    def test_full_profile_matches_frozen_catalog(self, tmp_path: Path) -> None:
        manifest = build_contract_corpus(tmp_path, full=True)
        expected = json.loads(
            (BACKEND_DIR / "benchmarks/mesh/corpus-v2.json").read_text()
        )
        assert json.loads(json.dumps(asdict(manifest))) == expected
        verify_manifest(tmp_path, manifest)
        full = [row for row in manifest.fixtures if row.profile is Profile.FULL]
        assert len(full) == 6
        for entry in full:
            with (tmp_path / entry.filename).open("rb") as source:
                assert hashlib.file_digest(source, "sha256").hexdigest() == entry.sha256
            assert (tmp_path / entry.filename).stat().st_size == entry.input_bytes

    def test_preserves_v1_content(self, corpus_v2, tmp_path: Path) -> None:
        root, manifest = corpus_v2
        old_root = tmp_path / "old"
        old = build_v1(old_root)
        entries = {entry.filename: entry for entry in manifest.fixtures}
        for entry in old.fixtures:
            assert (root / entry.filename).read_bytes() == (
                old_root / entry.filename
            ).read_bytes()
            assert entries[entry.filename].sha256 == entry.sha256
            assert entries[entry.filename].origin == entry.origin

    def test_repeats_identical_fixture_bytes(self, corpus_v2, tmp_path: Path) -> None:
        root, manifest = corpus_v2
        repeated_root = tmp_path / "repeated"
        assert build_contract_corpus(repeated_root) == manifest
        for entry in manifest.fixtures:
            assert (root / entry.filename).read_bytes() == (
                repeated_root / entry.filename
            ).read_bytes()

    def test_hashes_actual_generated_inputs(self, corpus_v2) -> None:
        root, manifest = corpus_v2
        for entry in manifest.fixtures:
            content = (root / entry.filename).read_bytes()
            assert entry.input_bytes == len(content)
            assert entry.sha256 == hashlib.sha256(content).hexdigest()
            assert entry.license == "AGPL-3.0"

    def test_describes_required_families(self, corpus_v2) -> None:
        _, manifest = corpus_v2
        assert {entry.family for entry in manifest.fixtures} == set(Family)
        assert manifest.expectation_scope == "target_contract_not_observed_compliance"

    def test_retains_profile_boundaries(self, corpus_v2) -> None:
        root, manifest = corpus_v2
        catalog = json.loads(
            (BACKEND_DIR / "benchmarks/mesh/corpus-v2.json").read_text()
        )
        assert {entry.profile for entry in manifest.fixtures} == {Profile.SMALL}
        full = [
            row["filename"] for row in catalog["fixtures"] if row["profile"] == "full"
        ]
        assert set(full) == {
            "load-5000.stl",
            "load-20000.stl",
            "load-200000.stl",
            "load-1000000.stl",
            "load-2000000.stl",
            "ascii-large.stl",
        }
        assert not any(((root / name).exists() for name in full))


class TestBinaryFixtures:
    @pytest.mark.parametrize(
        ("filename", "normal", "attribute", "count", "trailing"),
        [
            ("binary-unreliable-normals.stl", (123, -77, 42), 0, 12, 0),
            ("binary-face-attributes.stl", (0, 0, 0), 65535, 12, 0),
            ("binary-degenerate.stl", (0, 0, 0), 0, 13, 0),
            ("binary-trailing.stl", (0, 0, 0), 0, 12, 25),
        ],
    )
    def test_encodes_binary_policy_cases(
        self, corpus_v2, filename, normal, attribute, count, trailing
    ) -> None:
        root, _ = corpus_v2
        content = (root / filename).read_bytes()
        assert struct.unpack_from("<I", content, 80)[0] == count
        assert struct.unpack_from("<3f", content, 84) == normal
        assert struct.unpack_from("<H", content, 132)[0] == attribute
        assert len(content) == 84 + count * 50 + trailing

    @pytest.mark.parametrize("filename", ["binary-nan.stl", "binary-inf.stl"])
    def test_encodes_nonfinite_coordinates(self, corpus_v2, filename) -> None:
        root, manifest = corpus_v2
        content = (root / filename).read_bytes()
        assert not math.isfinite(struct.unpack_from("<f", content, 96)[0])
        entry = next((row for row in manifest.fixtures if row.filename == filename))
        assert entry.expectation.outcome == "refuse"
        assert entry.expectation.rule == "nonfinite_coordinates"


class TestAsciiFixtures:
    @pytest.mark.parametrize(
        ("filename", "facets", "solids", "max_line_min"),
        [
            ("ascii-whitespace.stl", 12, 1, 0),
            ("ascii-empty.stl", 0, 0, 0),
            ("ascii-long-line.stl", 12, 1, 8192),
            ("ascii-multiple-solids.stl", 24, 2, 0),
            ("ascii-incomplete.stl", 1, 1, 0),
        ],
    )
    def test_encodes_ascii_policy_cases(
        self, corpus_v2, filename, facets, solids, max_line_min
    ) -> None:
        root, _ = corpus_v2
        text = (root / filename).read_text()
        assert text.count("facet normal") == facets
        assert sum((line.startswith("solid ") for line in text.splitlines())) == solids
        assert max(map(len, text.splitlines()), default=0) >= max_line_min


class TestGeometryFixtures:
    @pytest.mark.parametrize(
        ("filename", "count", "bounds", "volume"),
        [
            ("geometry-sphere-octahedron.stl", 8, (2, 2, 2), 4 / 3),
            ("geometry-torus-square.stl", 32, (4, 4, 1), 12),
            (
                "geometry-remote-tiny-component.stl",
                24,
                (1000.125, 20, 20),
                8000 + 0.125**3,
            ),
            ("geometry-thin-solid.stl", 12, (20, 20, 0.125), 50),
            ("geometry-duplicate-vertices.stl", 12, (20, 20, 20), 8000),
        ],
    )
    def test_matches_analytic_geometry(
        self, corpus_v2, filename, count, bounds, volume
    ) -> None:
        root, manifest = corpus_v2
        triangles = _triangles(root / filename)
        vertices = [point for triangle in triangles for point in triangle]
        actual_bounds = tuple(
            (
                max((p[i] for p in vertices)) - min((p[i] for p in vertices))
                for i in range(3)
            )
        )
        actual_volume = sum(
            (
                (
                    a[0] * (b[1] * c[2] - b[2] * c[1])
                    + a[1] * (b[2] * c[0] - b[0] * c[2])
                    + a[2] * (b[0] * c[1] - b[1] * c[0])
                )
                / 6
                for a, b, c in triangles
            )
        )
        assert len(triangles) == count
        assert actual_bounds == bounds
        assert actual_volume == pytest.approx(volume)
        entry = next((row for row in manifest.fixtures if row.filename == filename))
        assert entry.expectation.volume_mm3 == pytest.approx(volume)

    @pytest.mark.parametrize(
        "name",
        [
            "open-cube",
            "reversed-winding",
            "flat-surface",
            "overlapping-solids",
            "nonmanifold-edge",
        ],
    )
    def test_marks_uncertifiable_volume_unknown(self, corpus_v2, name) -> None:
        _, manifest = corpus_v2
        entry = next(
            (row for row in manifest.fixtures if row.filename == f"geometry-{name}.stl")
        )
        assert entry.expectation.volume_mm3 is None
        assert entry.expectation.volume_contract == "unknown"

    @pytest.mark.parametrize(
        ("name", "count", "unique_vertices"),
        [
            ("open-cube", 10, 8),
            ("flat-surface", 2, 4),
            ("nonmanifold-edge", 13, 8),
            ("overlapping-solids", 24, 16),
        ],
    )
    def test_encodes_topology_cases(
        self, corpus_v2, name, count, unique_vertices
    ) -> None:
        root, _ = corpus_v2
        triangles = _triangles(root / f"geometry-{name}.stl")
        assert len(triangles) == count
        assert len({point for face in triangles for point in face}) == unique_vertices


class TestPrecisionFixtures:
    def test_retains_float64_source_coordinates(
        self, corpus_v2, read_3mf_model
    ) -> None:
        root, manifest = corpus_v2
        model = read_3mf_model(root / "scene-source_translation.3mf")
        xs = {float(v.attrib["x"]) for v in model.findall(".//{*}vertex")}
        assert xs == {1000000000000.0, 1000000000000.0 + 20}
        entry = next(
            (
                row
                for row in manifest.fixtures
                if row.filename == "scene-source_translation.3mf"
            )
        )
        assert entry.expectation.bbox_mm == (20, 20, 20)

    @pytest.mark.parametrize(
        ("unit", "scale"),
        [
            ("micron", 0.001),
            ("millimeter", 1),
            ("centimeter", 10),
            ("inch", 25.4),
            ("foot", 304.8),
            ("meter", 1000),
        ],
    )
    def test_encodes_equivalent_units(
        self, corpus_v2, unit, scale, read_3mf_model
    ) -> None:
        root, _ = corpus_v2
        model = read_3mf_model(root / f"precision-equivalent-{unit}.3mf")
        xs = [float(v.attrib["x"]) for v in model.findall(".//{*}vertex")]
        assert model.attrib["unit"] == unit
        assert (max(xs) - min(xs)) * scale == pytest.approx(20)

    def test_retains_permuted_geometry(self, corpus_v2) -> None:
        root, _ = corpus_v2
        expected = _triangles(root / "cube-binary.stl")
        permuted = _triangles(root / "precision-permuted.stl")
        assert expected == tuple(reversed(permuted))
        assert (root / "cube-binary.stl").read_bytes() != (
            root / "precision-permuted.stl"
        ).read_bytes()


class TestLoadFixtures:
    @pytest.mark.parametrize(("width", "height"), [(1, 1), (50, 50), (100, 100)])
    def test_writes_exact_grid_counts(self, width, height) -> None:
        output = io.BytesIO()
        write_grid(output, width=width, height=height)
        content = output.getvalue()
        assert struct.unpack_from("<I", content, 80)[0] == width * height * 2
        assert len(content) == 84 + width * height * 100
        assert struct.unpack_from("<3f", content, 96) == (0, 0, 0)

    def test_bounds_write_chunks(self) -> None:

        class Sink(io.RawIOBase):
            def __init__(self):
                self.lengths = []

            def write(self, content):
                self.lengths.append(len(content))
                return len(content)

        output = Sink()
        write_grid(output, width=500, height=200)
        assert max(output.lengths) <= WRITE_CHUNK_BYTES
        assert sum(output.lengths) == 84 + 200000 * 50

    def test_encodes_many_instances(self, corpus_v2, read_3mf_model) -> None:
        root, manifest = corpus_v2
        model = read_3mf_model(root / "scene-many_instances.3mf")
        assert len(model.findall(".//{*}object")) == 1
        assert len(model.findall(".//{*}item")) == 2048
        entry = next(
            (
                row
                for row in manifest.fixtures
                if row.filename == "scene-many_instances.3mf"
            )
        )
        assert entry.expectation.triangle_count == 24576
        assert entry.source_faces == 12

    def test_bounds_compressed_xml(self, corpus_v2) -> None:
        root, _ = corpus_v2
        with zipfile.ZipFile(root / "scene-high_compression.3mf") as package:
            info = package.getinfo("3D/3dmodel.model")
            assert 1000000 < info.file_size < 2000000
            assert info.compress_size < info.file_size / 100
            assert len(package.read(info)) == info.file_size

    @pytest.mark.parametrize(
        ("width", "height"), [(0, 1), (-1, 1), (True, 1), (1.5, 1), (1001, 1000)]
    )
    def test_refuses_invalid_grid_before_output(self, width, height) -> None:
        output = io.BytesIO()
        with pytest.raises(ValueError, match="invalid grid size"):
            write_grid(output, width=width, height=height)
        assert output.getvalue() == b""

    def test_writes_large_ascii_counts(self) -> None:
        output = io.BytesIO()
        write_grid(output, width=100, height=100, ascii_format=True)
        content = output.getvalue()
        assert content.count(b"facet normal") == 20000
        assert content.count(b"vertex") == 60000
        assert content.startswith(b"solid grid\n")
        assert content.endswith(b"endsolid grid\n")


class TestRecoveryScenarios:
    def test_records_required_failure_scenarios(self) -> None:
        scenarios = recovery_scenarios()
        assert {row.kind for row in scenarios} == set(RecoveryKind)
        assert len(scenarios) == len(RecoveryKind)
        assert all((row.precondition and row.expected_contract for row in scenarios))
        assert {row.execution for row in scenarios} == {
            "harness_scenario_not_a_fixture"
        }


class TestVerifyManifest:
    def test_rejects_changed_fixture(self, corpus_v2) -> None:
        root, manifest = corpus_v2
        (root / manifest.fixtures[0].filename).write_bytes(b"changed")
        with pytest.raises(ValueError, match="fixture content differs"):
            verify_manifest(root, manifest)
