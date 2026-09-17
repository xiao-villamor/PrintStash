"""Functional language can match metadata without changing literal name search."""

import pytest

from printstash_core.search.concepts import alternatives


class TestConcepts:
    def test_recognizes_metadata_for_holding_objects(self):
        assert ("cradle",) in alternatives("HOLDER")
        assert ("stand",) in alternatives("holder")

    @pytest.mark.parametrize(
        "query", ["goose", "phone holder", "specter", "", "bolt size"]
    )
    def test_leaves_unrecognized_queries_literal(self, query):
        assert alternatives(query) == ()

    def test_requires_the_functional_qualification(self):
        assert alternatives("gear") == (("toothed", "wheel"),)
        assert alternatives("bolt") == (("threaded", "screw"),)
