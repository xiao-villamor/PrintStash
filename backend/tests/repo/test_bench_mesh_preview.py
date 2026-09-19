"""The mesh benchmark requires deterministic geometry and preview outputs."""

from __future__ import annotations

import copy

import pytest

from scripts.bench_mesh_preview import _assert_compatible, run


class TestMeshPreviewBenchmark:
    def test_records_complete_mesh_preview_workloads(self) -> None:
        first = run(1, quick=True)
        second = run(1, quick=True)

        assert first["measurement_protocol"] == "mesh-preview-v1"
        assert first["source_hashes"] == second["source_hashes"]
        assert first["correctness_catalog"] == second["correctness_catalog"]
        assert set(first["workloads"]) == {
            "small_binary_stl",
            "dense_binary_stl",
            "ascii_stl",
            "multipart_3mf",
            "embedded_3mf",
        }
        assert all(
            item["complete"] and item["image_bytes"] > 0
            for item in first["correctness_catalog"].values()
        )
        _assert_compatible(first, second)

    def test_refuses_changed_mesh_geometry(self) -> None:
        reference = run(1, quick=True)
        changed = copy.deepcopy(reference)
        changed["correctness_catalog"]["small_binary_stl"]["geometry"][
            "triangle_count"
        ] += 1

        with pytest.raises(ValueError, match="geometry"):
            _assert_compatible(reference, changed)
