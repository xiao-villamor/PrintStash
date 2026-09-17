"""Name tolerance accepts a single edit without weakening short keyword matching."""

import pytest

from printstash_core.search.names import candidate_prefixes, one_edit_apart


class TestOneEditApart:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("specter", "spectre"),
            ("holder", "holer"),
            ("holder", "hollder"),
            ("holder", "holfer"),
            ("holder", "holders"),
            ("holder", "xholder"),
        ],
        ids=["transpose", "delete", "insert", "substitute", "suffix", "prefix"],
    )
    def test_accepts_one_edit(self, left, right):
        assert one_edit_apart(left, right)

    @pytest.mark.parametrize(
        ("left", "right"),
        [("holder", "holder"), ("holder", "hacker"), ("goose", "looser")],
        ids=["equal", "two-substitutions", "two-edits"],
    )
    def test_rejects_non_matches(self, left, right):
        assert not one_edit_apart(left, right)


class TestCandidatePrefixes:
    @pytest.mark.parametrize(
        "token", ["cat", "", "boat"], ids=["short", "empty", "four"]
    )
    def test_excludes_short_queries(self, token):
        assert candidate_prefixes(token) == ()

    def test_includes_leading_edits(self):
        prefixes = candidate_prefixes("specter")
        assert {"sp", "ps", "pe", "sc", "xp", "xs"} <= set(prefixes)
        assert all(len(prefix) == 2 for prefix in prefixes)
        assert len(prefixes) < 120
