"""The similarity benchmark requires stable geometry and decisions."""

from __future__ import annotations

import copy
import json

import pytest

from scripts.bench_similarity import _assert_compatible, run


class TestSimilarityBenchmark:
    def test_records_similarity_correctness(self) -> None:
        first = run(1, quick=True)
        second = run(1, quick=True)

        assert first["measurement_protocol"] == "geometric-similarity-v1"
        assert first["source_hashes"] == second["source_hashes"]
        assert set(first["correctness_catalog"]["fingerprints"]) == {
            "tetrahedron",
            "subdivided",
        }
        verifications = first["correctness_catalog"]["verifications"]
        assert verifications["identical"]["evidence_class"] == "identical_geometry"
        assert verifications["rescaled_mirrored"]["evidence_class"] == (
            "rescaled_mirrored"
        )
        assert verifications["retessellated"]["evidence_class"] == "remeshed"
        assert verifications["similar_shape"]["evidence_class"] == "similar_shape"
        assert json.loads(json.dumps(first))["measurement_protocol"] == (
            "geometric-similarity-v1"
        )
        _assert_compatible(first, second)

    def test_refuses_changed_similarity_decision(self) -> None:
        reference = run(1, quick=True)
        changed = copy.deepcopy(reference)
        changed["correctness_catalog"]["verifications"]["identical"][
            "evidence_class"
        ] = "similar_shape"

        with pytest.raises(ValueError, match="evidence_class"):
            _assert_compatible(reference, changed)
