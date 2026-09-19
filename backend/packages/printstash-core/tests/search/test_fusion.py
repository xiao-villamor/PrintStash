"""Known ordinal lists specify fusion independent of vector magnitudes."""

import pytest

from printstash_core.search.fusion import RankedLeg, fuse
from printstash_core.search.passages import SearchSubject, SubjectType


class TestFuse:
    def test_fuses_ranked_subjects(self):
        first, second, third = (
            SearchSubject(SubjectType.MODEL, id) for id in (1, 2, 3)
        )

        result = fuse(
            (
                RankedLeg("lexical", (first, first, second)),
                RankedLeg("semantic_text", (second, third), 2),
            )
        )

        assert [item.subject for item in result] == [second, third, first]
        assert result[0].score == pytest.approx(1 / 62 + 2 / 61)
        assert result[0].legs == ("lexical", "semantic_text")

    @pytest.mark.parametrize(
        "weight, budgets",
        [
            (0, {}),
            (-1, {}),
            (float("nan"), {}),
            (float("inf"), {}),
            (11, {}),
            (1, {"k": 0}),
            (1, {"k": 1001}),
            (1, {"limit": 0}),
            (1, {"limit": 2049}),
        ],
    )
    def test_rejects_invalid_fusion_budgets(self, weight, budgets):
        with pytest.raises(ValueError, match="search_fusion_invalid"):
            fuse((RankedLeg("lexical", (), weight),), **budgets)

    def test_orders_equal_scores_by_typed_subject(self):
        model = SearchSubject(SubjectType.MODEL, 1)
        document = SearchSubject(SubjectType.DOCUMENT, 1)
        assert [
            item.subject
            for item in fuse(
                (
                    RankedLeg("lexical", (model,)),
                    RankedLeg("semantic_text", (document,)),
                )
            )
        ] == [document, model]

    def test_accepts_an_empty_library(self):
        assert fuse((RankedLeg("lexical", ()),)) == ()
