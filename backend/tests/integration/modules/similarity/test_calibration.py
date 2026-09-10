"""Frozen design-held-out quality gates use real geometry, never mocked scores."""

import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pytest
from printstash_core.mesh.similarity.verification import (
    VERIFICATION_VERSION,
    verify_meshes,
)

from app.modules.media.fingerprints import ALGORITHM_VERSION
from tests.factories.similarity_corpus import ROOT, cases, mesh_for, provenance


@pytest.fixture(scope="module")
def evaluation():
    measured = []
    for row in cases("evaluation"):
        first, second = (
            mesh_for(row["base"], row["left_variant"]),
            mesh_for(row["base"], row["right_variant"]),
        )
        evidence = verify_meshes(
            first.vertices, first.faces, second.vertices, second.faces
        )
        measured.append({**row, "evidence": asdict(evidence)})
    output = os.environ.get("SIMILARITY_CALIBRATION_REPORT")
    if output:
        confusion = Counter(
            (row["expected"], row["evidence"]["evidence_class"] or "none")
            for row in measured
        )
        Path(output).write_text(
            json.dumps(
                {
                    "algorithm_version": ALGORITHM_VERSION,
                    "verification_version": VERIFICATION_VERSION,
                    "cases": measured,
                    "confusion": [
                        {"expected": a, "predicted": b, "count": n}
                        for (a, b), n in sorted(confusion.items())
                    ],
                },
                indent=2,
            )
            + "\n"
        )
    return measured


class TestFrozenCorpus:
    def test_verifies_licensed_source_digests(self):
        for item in provenance()["files"]:
            assert item["license"] == "CC0-1.0"
            assert (
                hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest()
                == item["sha256"]
            )
            assert item["author"] and item["upstream"] and item["metadata_source"]

    def test_keeps_design_derivatives_in_one_split(self):
        owners = {}
        for row in cases():
            previous = owners.setdefault(row["design_id"], row["split"])
            assert row["split"] == previous
        assert len([split for split in owners.values() if split == "evaluation"]) >= 3

    @pytest.mark.parametrize("label", ["identical_geometry", "rescaled"])
    @pytest.mark.critical
    def test_meets_exact_class_precision(self, evaluation, label):
        predicted = [
            row for row in evaluation if row["evidence"]["evidence_class"] == label
        ]
        assert predicted, "precision is not established by producing no predictions"
        precision = sum(row["expected"] == label for row in predicted) / len(predicted)
        assert precision >= 0.99, predicted
        assert all(
            row["evidence"]["exact_equivalence"] and row["evidence"]["confidence"] == 1
            for row in predicted
        )

    @pytest.mark.parametrize("label", ["remeshed", "repaired"])
    @pytest.mark.critical
    def test_meets_surface_class_recall(self, evaluation, label):
        expected = [row for row in evaluation if row["expected"] == label]
        assert expected
        recall = sum(
            row["evidence"]["evidence_class"] == label
            and row["evidence"]["confidence"] >= 0.9
            for row in expected
        ) / len(expected)
        assert recall >= 0.95, expected
        assert all(not row["evidence"]["exact_equivalence"] for row in expected)

    @pytest.mark.parametrize(
        "base", ["washer-96", "washer-192"], ids=["96-facets", "192-facets"]
    )
    @pytest.mark.critical
    def test_rejects_false_exact_washers(self, base):
        first, second = mesh_for(base, "original"), mesh_for(base, "hole_changed")

        evidence = verify_meshes(
            first.vertices, first.faces, second.vertices, second.faces
        )

        assert evidence.exact_equivalence is False
        assert evidence.evidence_class not in ("identical_geometry", "rescaled")

    def test_preserves_versioned_evaluation_golden(self, evaluation):
        golden = json.loads((ROOT / f"{ALGORITHM_VERSION}-evaluation.json").read_text())
        assert golden["verification_version"] == VERIFICATION_VERSION
        expected = {row["case_id"]: row["evidence"] for row in golden["cases"]}
        assert set(expected) == {row["case_id"] for row in evaluation}
        for row in evaluation:
            actual = row["evidence"]
            frozen = expected[row["case_id"]]
            assert actual["evidence_class"] == frozen["evidence_class"], row["case_id"]
            assert actual["exact_equivalence"] == frozen["exact_equivalence"], row[
                "case_id"
            ]
            assert actual["confidence"] == pytest.approx(
                frozen["confidence"], abs=1e-5
            ), row["case_id"]
            fields = (
                "sampled_hausdorff",
                "sampled_chamfer",
                "voxel_iou",
                "scale_factor",
                "sampled_surface_chamfer",
                "sampled_surface_hausdorff",
            )
            assert {key: actual[key] for key in fields} == pytest.approx(
                {key: frozen[key] for key in fields}, abs=1e-5
            ), row["case_id"]

    def test_keeps_chiral_mirrors_distinct(self, evaluation):
        mirrors = [
            row
            for row in evaluation
            if row["expected"] in ("mirrored", "rescaled_mirrored")
        ]
        assert len(mirrors) == 2
        assert all(
            row["evidence"]["evidence_class"] == row["expected"] for row in mirrors
        )
        assert all(not row["evidence"]["mirror_ambiguous"] for row in mirrors)
