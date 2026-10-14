"""Placing a print's material requirements on a printer's loaded feeds.

Two printer shapes need different answers. On one shared tool (an AMS) any
feed reaches the extruder, so a print needs a distinct feed per requirement and
the tool index only names the slicer's slot. On several tools (a toolchanger)
requirement `N` can only be fed by a feed on `toolN`. Colour never decides
whether a print fits; it comes back as an advisory per requirement.
"""

from __future__ import annotations

import pytest

from app.modules.printing.feed_fit import (
    Feed,
    FeedFit,
    Need,
    fit_requirements,
)


def feed(material: str, color: str | None = None, tool: str | None = "tool0") -> Feed:
    return Feed(material, color, tool)


class TestSharedTool:
    def test_fits_when_each_requirement_has_its_own_feed(self) -> None:
        result = fit_requirements(
            [Need(0, "PLA", None), Need(1, "PETG", None)],
            [feed("PETG"), feed("PLA")],
        )

        assert result.fit == FeedFit.FITS
        assert result.short_types == ()

    def test_a_slicer_slot_index_beyond_the_feed_count_still_fits(self) -> None:
        # Slot 3 of the slicer says nothing about which tray holds the PLA.
        result = fit_requirements([Need(2, "PLA", None)], [feed("PETG"), feed("PLA")])

        assert result.fit == FeedFit.FITS

    def test_one_feed_cannot_serve_two_requirements(self) -> None:
        result = fit_requirements(
            [Need(0, "PLA", None), Need(1, "pla ", None)], [feed("PLA"), feed("PETG")]
        )

        assert result.fit == FeedFit.SHORT
        assert result.short_types == ("pla",)

    def test_matches_a_material_however_it_is_spelled(self) -> None:
        assert (
            fit_requirements([Need(0, " pla", None)], [feed("PLA ")]).fit
            == FeedFit.FITS
        )

    def test_a_missing_material_is_short(self) -> None:
        result = fit_requirements([Need(0, "ABS", None)], [feed("PLA")])

        assert (result.fit, result.short_types) == (FeedFit.SHORT, ("abs",))

    def test_no_feeds_leaves_every_requirement_short(self) -> None:
        result = fit_requirements([Need(0, "PLA", None), Need(1, "PETG", None)], [])

        assert result.short_types == ("petg", "pla")

    def test_same_colour_wins_the_feed_over_an_earlier_loose_match(self) -> None:
        result = fit_requirements(
            [Need(0, "PLA", "#00FF00"), Need(1, "PLA", "#FF0000")],
            [feed("PLA", "#FF0000"), feed("PLA", "#00FF00")],
        )

        assert result.color_advisories == ()

    def test_advises_the_feed_each_requirement_was_placed_on(self) -> None:
        result = fit_requirements(
            [Need(0, "PLA", "#FF0000"), Need(1, "PLA", "#00FF00")],
            [feed("PLA", "#111111"), feed("PLA", "#222222")],
        )

        assert result.color_advisories == (
            "tool0:#FF0000->#111111",
            "tool1:#00FF00->#222222",
        )

    @pytest.mark.parametrize(
        ("need", "loaded"),
        [(None, "#111111"), ("#FF0000", None)],
        ids=["no-need", "no-feed"],
    )
    def test_no_advice_without_both_colours(
        self, need: str | None, loaded: str | None
    ) -> None:
        result = fit_requirements([Need(0, "PLA", need)], [feed("PLA", loaded)])

        assert result.color_advisories == ()

    def test_feeds_with_no_tool_are_treated_as_one_pool(self) -> None:
        result = fit_requirements(
            [Need(0, "PLA", None), Need(1, "PETG", None)],
            [feed("PLA", tool=None), feed("PETG", tool=None)],
        )

        assert result.fit == FeedFit.FITS


class TestSeveralTools:
    def test_each_requirement_needs_its_own_tool(self) -> None:
        result = fit_requirements(
            [Need(0, "PLA", None), Need(1, "PETG", None)],
            [feed("PLA", tool="tool0"), feed("PETG", tool="tool1")],
        )

        assert result.fit == FeedFit.FITS

    def test_a_tool_the_printer_does_not_report_is_unmapped(self) -> None:
        result = fit_requirements(
            [Need(0, "PLA", None), Need(2, "PETG", None)],
            [feed("PLA", tool="tool0"), feed("PETG", tool="tool1")],
        )

        assert (result.fit, result.short_types) == (FeedFit.UNMAPPED, ())

    def test_the_wrong_material_on_the_tool_is_short(self) -> None:
        result = fit_requirements(
            [Need(0, "PLA", None), Need(1, "PETG", None)],
            [feed("PLA", tool="tool0"), feed("ABS", tool="tool1")],
        )

        assert (result.fit, result.short_types) == (FeedFit.SHORT, ("petg",))

    def test_short_outranks_unmapped(self) -> None:
        result = fit_requirements(
            [Need(1, "PETG", None), Need(5, "PLA", None)],
            [feed("PLA", tool="tool0"), feed("ABS", tool="tool1")],
        )

        assert result.fit == FeedFit.SHORT

    def test_advises_the_colour_of_the_feed_on_the_tool(self) -> None:
        result = fit_requirements(
            [Need(0, "PLA", "#FF0000"), Need(1, "PLA", "#00FF00")],
            [feed("PLA", "#FF0000", "tool0"), feed("PLA", "#222222", "tool1")],
        )

        assert result.color_advisories == ("tool1:#00FF00->#222222",)
