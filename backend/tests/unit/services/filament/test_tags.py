"""Defends tags at the services filament unit boundary.

A regression would misstate filament identity, conversion, or profile metadata to callers.
"""

from __future__ import annotations

from ._helpers_shared import (
    parse_tag_input,
    pytest,
)


class TestParseTagInput:
    def test_splits_and_trims(self) -> None:
        assert parse_tag_input("a, b ,c") == ["a", "b", "c"]

    def test_drops_empty_segments(self) -> None:
        assert parse_tag_input("a, ,,b,") == ["a", "b"]

    @pytest.mark.parametrize("value", [None, "", "   ", ",,,"])
    def test_empty_inputs_return_empty_list(self, value) -> None:
        assert parse_tag_input(value) == []
