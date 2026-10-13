"""The disposable pilot reproduces current geometry before reporting cache costs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from scripts.mesh_benchmark_corpus import build_contract_corpus
from tests.paths import BACKEND_DIR


class TestPrepareSource:
    @pytest.mark.parametrize(
        "filename",
        ["cube-mm.3mf", "cube-inch.3mf", "reflected-build.3mf", "multiple-build.3mf"],
        ids=["millimeters", "inches", "reflection", "multiple-build"],
    )
    def test_preserves_parsed_geometry(self, tmp_path, filename):
        from scripts.bench_prepared_cache import (
            CacheIdentity,
            prepare_source,
            read_cache,
            write_cache,
        )

        corpus = tmp_path / "source"
        corpus.mkdir()
        manifest = build_contract_corpus(corpus)
        source = corpus / filename
        original = source.read_bytes()
        entry = next(item for item in manifest.fixtures if item.filename == filename)
        identity = CacheIdentity(
            source_sha256=entry.sha256,
            parser_version="mesh-load-v1",
            representation_version="arrays-f64-i64-v1",
            parameters=("file_type=3mf", "process=False"),
        )
        parsed = prepare_source(source, file_type="3mf")
        expected_vertices, expected_faces = parsed.vertices.copy(), parsed.faces.copy()
        expected_stl = hashlib.sha256(parsed.export(file_type="stl")).hexdigest()

        write_cache(tmp_path / "cache", identity, parsed)
        restored = read_cache(tmp_path / "cache", identity)

        np.testing.assert_array_equal(restored.vertices, expected_vertices)
        np.testing.assert_array_equal(restored.faces, expected_faces)
        assert (
            hashlib.sha256(restored.export(file_type="stl")).hexdigest() == expected_stl
        )
        assert len(restored.faces) == entry.expectation.triangle_count
        np.testing.assert_allclose(
            np.ptp(restored.vertices, axis=0), entry.expectation.bbox_mm
        )
        np.testing.assert_array_equal(parsed.vertices, expected_vertices)
        np.testing.assert_array_equal(parsed.faces, expected_faces)
        assert source.read_bytes() == original
        assert hashlib.sha256(original).hexdigest() == identity.source_sha256


class TestMain:
    def test_emits_one_bounded_cold_warm_observation(self, tmp_path):
        from app.modules.derivatives.kinds import (
            MESH_GEOMETRY_RECIPE,
            MESH_THUMBNAIL_RECIPE,
            VIEWER_STL_RECIPE,
        )
        from app.modules.media.fingerprints import ALGORITHM_VERSION

        output = tmp_path / "pilot"

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.bench_prepared_cache",
                "--output-dir",
                str(output),
                "--repeat",
                "1",
                "--cold-runs",
                "1",
                "--case",
                "cube-mm.3mf",
            ],
            cwd=BACKEND_DIR,
            env={
                **os.environ,
                "PYTHONPATH": str(BACKEND_DIR),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout)
        assert report == json.loads((output / "report.json").read_text())
        assert report["schema_version"] == 1
        assert report["scope"] == "private_pilot_no_production_adoption"
        assert report["observed_user_reuse"] is False
        assert (report["repeat"], report["cold_runs"]) == (1, 1)
        assert report["environment"]["python_version"]
        assert report["recipes"] == {
            "metadata": MESH_GEOMETRY_RECIPE,
            "thumbnail": MESH_THUMBNAIL_RECIPE,
            "viewer": VIEWER_STL_RECIPE,
            "fingerprint": ALGORITHM_VERSION,
        }
        assert set(report["cases"]) == {"cube-mm.3mf"}
        case = report["cases"]["cube-mm.3mf"]
        source = output / "sources" / "cube-mm.3mf"
        assert (
            case["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        )
        assert case["source"]["input_bytes"] == source.stat().st_size
        assert case["cache_bytes"] > 0
        assert set(case["warm"]) == {"source", "cache", "creation"}
        assert set(case["cold"]) == {"source", "cache"}
        samples = [
            *case["warm"]["source"],
            *case["warm"]["cache"],
            *case["warm"]["creation"],
            *case["cold"]["source"],
            *case["cold"]["cache"],
        ]
        assert len(samples) == 5
        assert [sample["outcome"] for sample in samples] == ["completed"] * 5
        assert all(sample["elapsed_ms"] >= 0 for sample in samples)
        assert all(sample["peak_rss_bytes"] > 0 for sample in samples)
        assert case["warm"]["creation"][0]["cache_bytes"] == case["cache_bytes"]
        consumers = [samples[0], samples[1], samples[3], samples[4]]
        assert [sample["size"] for sample in consumers] == [84 + 12 * 50] * 4
        assert [sample["sha256"] for sample in consumers] == [
            case["reference"]["sha256"]
        ] * 4
        assert case["reference"]["size"] == 84 + 12 * 50
        assert all(sample["total_ms"] >= sample["elapsed_ms"] for sample in samples[3:])
