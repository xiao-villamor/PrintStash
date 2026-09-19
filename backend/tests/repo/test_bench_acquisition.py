"""The acquisition benchmark rejects changed bytes, names, and request counts."""

from __future__ import annotations

import copy

import pytest

from scripts.bench_acquisition import _assert_compatible, run


class TestAcquisitionBenchmark:
    def test_records_acquisition_correctness(self) -> None:
        first = run(1, quick=True)
        second = run(1, quick=True)

        assert first["measurement_protocol"] == "native-acquisition-v1"
        assert first["network_counters"] == {"requests": 4, "bytes": 528384}
        assert first["correctness_catalog"] == second["correctness_catalog"]
        assert set(first["download_latency"]) == {"small", "large", "redirect"}
        _assert_compatible(first, second)

    def test_refuses_changed_acquisition_output(self) -> None:
        reference = run(1, quick=True)
        changed = copy.deepcopy(reference)
        changed["correctness_catalog"]["large"]["sha256"] = "0" * 64

        with pytest.raises(ValueError, match="correctness_catalog"):
            _assert_compatible(reference, changed)
