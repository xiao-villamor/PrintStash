"""Lexical query literals and the declared cross-dialect BM25 reference."""

import pytest

from printstash_core.search.lexical import bm25, frequencies, fts_query, query_terms


class TestLexical:
    def test_tokenizes_unicode_query_without_operators(self):
        assert query_terms('"Dragón" OR wall-mounted + clip_*') == (
            "dragón",
            "or",
            "wall",
            "mounted",
            "clip",
        )
        assert (
            fts_query('"Dragón" OR wall-mounted + clip_*')
            == '"dragón" OR "or" OR "wall" OR "mounted" OR "clip"'
        )

    def test_computes_reference_bm25(self):
        assert bm25(
            frequency=5,
            document_length=20,
            document_count=10,
            document_frequency=2,
            total_length=150,
        ) == pytest.approx(2.0710045702066546)

    def test_weights_title_more_than_body(self):
        length, counts = frequencies("Bracket", "flexible", "bracket")
        assert length == 3
        assert counts == {"bracket": 6.0, "flexible": 3.0}

    def test_bounds_query_terms(self):
        assert len(query_terms(" ".join(str(i) for i in range(50)))) == 32

    def test_refuses_an_oversized_query(self):
        with pytest.raises(ValueError, match="search_query_too_long"):
            query_terms("q" * 513)

    def test_returns_zero_for_an_empty_corpus(self):
        assert (
            bm25(
                frequency=1,
                document_length=1,
                document_count=0,
                document_frequency=0,
                total_length=0,
            )
            == 0
        )

    def test_rejects_impossible_corpus_statistics(self):
        with pytest.raises(ValueError, match="search_corpus_statistics_invalid"):
            bm25(
                frequency=1,
                document_length=1,
                document_count=1,
                document_frequency=2,
                total_length=1,
            )

    def test_clamps_common_term_idf(self):
        assert bm25(
            frequency=1,
            document_length=1,
            document_count=1,
            document_frequency=1,
            total_length=1,
        ) == pytest.approx(1e-6)
