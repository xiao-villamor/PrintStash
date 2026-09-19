"""The archive benchmark records reproducible output and bounded rejection."""

from __future__ import annotations

from scripts.bench_archive import run


class TestArchiveBenchmark:
    def test_records_archive_workloads_with_stable_results(self) -> None:
        first = run(1)
        second = run(1)

        assert first["measurement_protocol"] == "archive-extract-v1"
        assert first["source_hashes"] == second["source_hashes"]
        assert first["correctness_catalog"] == second["correctness_catalog"]
        assert first["rejection_catalog"] == {
            "oversized_entry": "archive_entry_too_large"
        }
        assert set(first["workloads"]) == {
            "many_small",
            "large_stored",
            "large_compressed",
        }
