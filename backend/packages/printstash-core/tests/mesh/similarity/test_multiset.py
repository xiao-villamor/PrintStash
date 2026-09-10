"""Quantity conservation and ambiguous matching decide containment."""

import pytest

from printstash_core.mesh.similarity.multiset import component_containment


class TestMultiset:
    def test_detects_six_copies(
        self,
    ):
        result = component_containment({1: 1}, {4: 6}, [(1, 4)])
        assert (result.evidence_class, result.contained_side, result.copies) == (
            "plate_of",
            "a",
            6,
        )
        assert result.assignments == ((1, 4, 1),)

    def test_reports_reverse_direction(
        self,
    ):
        result = component_containment({4: 6}, {1: 1}, [(4, 1)])
        assert (result.contained_side, result.copies) == ("b", 6)

    def test_requires_every_contained_component(
        self,
    ):
        assert component_containment({1: 1, 2: 1}, {3: 3}, [(1, 3)]) is None

    def test_does_not_collapse_quantities_to_sets(
        self,
    ):
        assert component_containment({1: 3}, {3: 2, 4: 2}, [(1, 3)]) is None

    def test_distinguishes_extra_unrelated_parts(
        self,
    ):
        result = component_containment({1: 1}, {3: 1, 4: 1}, [(1, 3)])
        assert (result.evidence_class, result.copies) == ("component_of", 1)

    def test_reassigns_ambiguous_correspondence(
        self,
    ):
        result = component_containment(
            {1: 1, 2: 1}, {3: 1, 4: 1, 5: 1}, [(1, 3), (1, 4), (2, 3)]
        )
        assert result.evidence_class == "component_of"
        assert set(result.assignments) == {(1, 4, 1), (2, 3, 1)}

    def test_does_not_treat_equal_multisets_as_whole_identity(
        self,
    ):
        assert component_containment({1: 1}, {2: 1}, [(1, 2)]) is None

    @pytest.mark.parametrize(
        "left,right,edges,reason",
        [
            ({}, {1: 1}, [], "budget"),
            ({i: 1 for i in range(257)}, {1: 1}, [], "budget"),
            ({1: 1}, {2: 2}, [(1, 2)] * 5121, "budget"),
            ({1: 0}, {2: 1}, [], "quantity"),
            ({1: True}, {2: 1}, [], "quantity"),
            ({1: 2049}, {2: 1}, [], "quantity"),
            ({1: 1}, {2: 1}, [(1, 3)], "edge"),
        ],
    )
    def test_rejects_unbounded_input(self, left, right, edges, reason):
        with pytest.raises(ValueError, match=reason):
            component_containment(left, right, edges)
