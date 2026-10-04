"""Source preflight stays bounded and cannot guess unknown geometry complexity."""

import os
from pathlib import Path

import pytest

from app.modules.media import mesh_processing, native_budget
from app.runtime.native_admission import Resources
from app.runtime.native_runtime import admit
from tests.factories.content import binary_stl


class TestEstimateSources:
    def test_reads_binary_count_through_a_stable_descriptor(self, tmp_path):
        source = tmp_path / "mesh.stl"
        source.write_bytes(binary_stl(triangles=2))
        pool = Resources(4, 4 * 1024**3)

        with source.open("rb") as stream:
            alias = Path(f"/proc/self/fd/{stream.fileno()}")
            amount = native_budget.estimate_sources(
                pool, (native_budget.MeshSource(alias, "stl"),)
            )

        assert amount == Resources(1, 512 * 1024**2)

    @pytest.mark.parametrize(
        "file_type,payload",
        [
            pytest.param("stl", b"solid text\nendsolid\n", id="ascii"),
            pytest.param("stl", b"truncated", id="malformed"),
            pytest.param("3mf", b"PK", id="archive"),
            pytest.param("obj", b"v 0 0 0\n", id="polygon"),
        ],
    )
    def test_unmeasurable_input_reserves_whole_pool(self, tmp_path, file_type, payload):
        source = tmp_path / "source"
        source.write_bytes(payload)
        pool = Resources(4, 4 * 1024**3)

        assert native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, file_type),)
        ) == Resources(1, pool.bytes)

    def test_missing_source_does_not_authorize_an_optimistic_share(self, tmp_path):
        pool = Resources(4, 4 * 1024**3)

        assert native_budget.estimate_sources(
            pool, (native_budget.MeshSource(tmp_path / "missing", "stl"),)
        ) == Resources(1, pool.bytes)

    def test_large_file_preflight_does_not_scan_facets(self, tmp_path):
        source = tmp_path / "large.stl"
        source.write_bytes(
            binary_stl(triangles=1)[:80] + (20_000_000).to_bytes(4, "little")
        )
        with source.open("r+b") as stream:
            stream.truncate(84 + 20_000_000 * 50)
        pool = Resources(4, 4 * 1024**3)

        assert (
            native_budget.estimate_sources(
                pool, (native_budget.MeshSource(source, "stl"),)
            ).bytes
            == pool.bytes
        )
        assert os.stat(source).st_blocks * 512 < 1024**2


class TestAdmittedGeometry:
    def test_loader_uses_its_weighted_allowance(self, monkeypatch):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        amount = Resources(1, 512 * 1024**2)
        with admit(amount, Resources(4, 4 * 1024**3), checkpoint=lambda: None):
            cap = mesh_processing._ram_triangle_cap(".stl")

        assert cap == (512 * 1024**2) // 2200
