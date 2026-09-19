"""The G-code benchmark records reproducible correctness and timing evidence."""

from __future__ import annotations

import shutil

from scripts.bench_gcode import run
from tests.paths import FIXTURES_DIR


class TestGcodeBenchmark:
    def test_records_parser_workloads_with_stable_results(self, tmp_path) -> None:
        shutil.copy2(FIXTURES_DIR / "sample.gcode", tmp_path / "sample.gcode")
        shutil.copy2(
            FIXTURES_DIR / "bgcode" / "prusaslicer.bgcode",
            tmp_path / "prusaslicer.bgcode",
        )
        first = run(tmp_path, 2)
        second = run(tmp_path, 2)

        assert first["measurement_protocol"] == "gcode-parse-v1"
        assert first["source_hashes"] == second["source_hashes"]
        assert first["metadata_catalog"] == second["metadata_catalog"]
        assert set(first["workloads"]) == {
            "small_text",
            "large_text",
            "official_bgcode",
            "malformed_bgcode",
        }
        assert (
            first["metadata_catalog"]["official_bgcode"]["slicer_name"] == "PrusaSlicer"
        )
        assert all(
            value is None
            for value in first["metadata_catalog"]["malformed_bgcode"].values()
        )
