"""Defends slugify at the services filament unit boundary.

A regression would misstate filament identity, conversion, or profile metadata to callers.
"""

from __future__ import annotations

from ._helpers_shared import (
    ensure_unique_slug,
    pytest,
    slugify,
)


class TestSlugify:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Hello World", "hello-world"),
            ("  Trim  ", "trim"),
            ("Über Box", "uber-box"),  # NFKD-folds accents to ASCII
            ("C++ Holder", "c-holder"),
            ("a---b", "a-b"),  # runs collapse
            ("MiXeD_Case", "mixed-case"),
            ("--leading-and-trailing--", "leading-and-trailing"),
        ],
    )
    def test_kebab_cases(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "已经", "🎉", "///"])
    def test_unsluggable_falls_back_to_model(self, raw: str) -> None:
        # Empty / non-ASCII-only input must never yield an empty slug.
        assert slugify(raw) == "model"


class TestEnsureUniqueSlug:
    def test_returns_base_when_free(self) -> None:
        assert ensure_unique_slug("benchy", lambda s: False) == "benchy"

    def test_appends_next_free_suffix(self) -> None:
        taken = {"benchy", "benchy-2"}
        assert ensure_unique_slug("benchy", lambda s: s in taken) == "benchy-3"

    def test_starts_numbering_at_two(self) -> None:
        # First collision becomes -2, never -1 / -0.
        assert ensure_unique_slug("x", lambda s: s == "x") == "x-2"
