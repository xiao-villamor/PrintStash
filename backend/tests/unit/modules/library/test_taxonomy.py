"""``subtree_totals`` turns per-collection counts into the sidebar's branch totals.

Each collection's badge is its own Models plus every descendant's. Before #295
this compared every path against every other on each request, which took a
minute on a 9k-collection library; the replacement must give the same answers.
"""

from __future__ import annotations

from app.modules.library.taxonomy import subtree_totals


class TestSubtreeTotals:
    def test_rolls_a_leaf_count_into_every_ancestor(self) -> None:
        totals = subtree_totals(
            {"parts": 1, "parts/brackets": 2, "parts/brackets/small": 4}
        )

        assert totals == {"parts": 7, "parts/brackets": 6, "parts/brackets/small": 4}

    def test_sums_sibling_branches_into_their_parent(self) -> None:
        totals = subtree_totals({"parts": 0, "parts/brackets": 2, "parts/gears": 3})

        assert totals["parts"] == 5

    def test_returns_an_empty_mapping_for_no_collections(self) -> None:
        assert subtree_totals({}) == {}

    def test_keeps_a_prefix_sibling_out_of_the_subtree(self) -> None:
        # "parts-extra" and "parts.old" sort between "parts" and "parts/…".
        totals = subtree_totals(
            {"parts": 1, "parts-extra": 10, "parts.old": 100, "parts/gears": 2}
        )

        assert totals == {
            "parts": 3,
            "parts-extra": 10,
            "parts.old": 100,
            "parts/gears": 2,
        }

    def test_skips_an_absent_parent_into_the_nearest_present_ancestor(self) -> None:
        # "parts/brackets" is trashed or not visible, so it is not in the input.
        totals = subtree_totals({"parts": 1, "parts/brackets/small": 4})

        assert totals == {"parts": 5, "parts/brackets/small": 4}

    def test_leaves_a_root_without_ancestors_at_its_own_count(self) -> None:
        totals = subtree_totals({"toys/dragons": 3})

        assert totals == {"toys/dragons": 3}
