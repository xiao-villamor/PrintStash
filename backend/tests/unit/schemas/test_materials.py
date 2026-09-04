"""Validation on the material-state payloads, before any row is written.

These are the checks that have to happen at the schema rather than in the
service, because they normalise as well as reject. `color_hex` is the case: a
colour arrives from three places — a slicer comment, a spool label, an operator
typing it — in three spellings of the same value, and comparison downstream is a
string comparison. Normalising to one canonical form here means the advisory in
`compatibility_for_printer` is comparing colours rather than spellings.

Blank is treated as absent rather than invalid, because a form field the operator
cleared means "I don't know", not "reject my whole update".
"""

from __future__ import annotations

import pytest

from app.schemas.materials import MaterialSlotWrite


def _slot(**overrides: object) -> MaterialSlotWrite:
    return MaterialSlotWrite(slot_key="feed", label="Feed", **overrides)  # type: ignore[arg-type]


class TestMaterialSlotWrite:
    def test_normalises_a_colour_to_upper_case_with_a_leading_hash(self) -> None:
        # Three sources spell the same colour three ways, and the advisory
        # downstream compares strings.
        assert _slot(color_hex="a1b2c3").color_hex == "#A1B2C3"

    def test_reads_a_blank_colour_as_no_colour(self) -> None:
        # A cleared form field means "I don't know", not "reject the update".
        assert _slot(color_hex=" ").color_hex is None

    def test_refuses_a_colour_that_is_not_a_colour(self) -> None:
        with pytest.raises(ValueError, match="material_color_invalid"):
            _slot(color_hex="not-a-color")
