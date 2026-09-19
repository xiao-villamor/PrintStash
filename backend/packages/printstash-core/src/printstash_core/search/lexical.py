"""Recipe-1 lexical terms and field-weighted BM25, independent of a database."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

TOKENIZER_VERSION = 1
MAX_QUERY_CHARS = 512
MAX_QUERY_TERMS = 32
TITLE_WEIGHT = 5.0
TAGS_WEIGHT = 3.0
TEXT_WEIGHT = 1.0
BM25_K1 = 1.2
BM25_B = 0.75
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def terms(text: str) -> tuple[str, ...]:
    """NFC Unicode words, punctuation-separated, matching simple FTS fixtures."""
    return tuple(
        token
        for token in _WORD.findall(unicodedata.normalize("NFC", text).lower())
        if len(token) <= 128
    )


def query_terms(query: str) -> tuple[str, ...]:
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError("search_query_too_long")
    return tuple(dict.fromkeys(terms(query)))[:MAX_QUERY_TERMS]


def fts_query(query: str) -> str:
    # Tokens contain letters/numbers only. Quotes still make the literal
    # contract explicit, including words named AND, OR and NEAR.
    return " OR ".join(
        '"' + term.replace('"', '""') + '"' for term in query_terms(query)
    )


def frequencies(title: str, tags: str, text: str) -> tuple[int, dict[str, float]]:
    weighted: dict[str, float] = {}
    length = 0
    for field, weight in (
        (title, TITLE_WEIGHT),
        (tags, TAGS_WEIGHT),
        (text, TEXT_WEIGHT),
    ):
        tokens = terms(field)
        length += len(tokens)
        for term, count in Counter(tokens).items():
            weighted[term] = weighted.get(term, 0.0) + count * weight
    return length, dict(weighted)


def bm25(
    *,
    frequency: float,
    document_length: int,
    document_count: int,
    document_frequency: int,
    total_length: int,
) -> float:
    """SQLite FTS5 BM25 sign reversed so higher values rank first.

    FTS5 clamps IDF at 1e-6 for very common terms. Matching that detail gives
    PostgreSQL the same controlled-corpus reference instead of relabeling ts_rank.
    """
    if frequency <= 0 or document_count <= 0 or total_length <= 0:
        return 0.0
    if not 0 <= document_frequency <= document_count or document_length < 0:
        raise ValueError("search_corpus_statistics_invalid")
    idf = max(
        1e-6,
        math.log(
            (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
        ),
    )
    average = total_length / document_count
    return (
        idf
        * frequency
        * (BM25_K1 + 1)
        / (frequency + BM25_K1 * (1 - BM25_B + BM25_B * document_length / average))
    )
