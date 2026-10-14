"""Whether a printer's loaded feeds can supply what a sliced print asks for.

A print asks for one material per slicer filament slot it uses
(`ArtifactMaterialRequirement`). A printer offers loaded feeds: one per spool
holder, tray or AMS slot. Two shapes of printer need different answers:

- **One shared tool** (a Bambu AMS, a MMU): every feed reaches the same
  extruder, so a print needs a *distinct* feed per requirement, whichever feed
  that is. Two PLA requirements need two PLA feeds; one PLA spool is not enough
  however often PLA is mentioned.
- **Several tools** (a toolchanger): requirement `N` can only be fed by a feed on
  `toolN`, so the feed's tool decides, and a print naming a tool the printer does
  not report cannot be placed.

Material comparison trims whitespace and ignores case. Colour never decides
whether a print fits; it is returned as an advisory.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FeedFit(StrEnum):
    FITS = "fits"
    SHORT = "short"
    UNMAPPED = "unmapped"


@dataclass(frozen=True)
class Need:
    tool_index: int
    material_type: str
    color_hex: str | None


@dataclass(frozen=True)
class Feed:
    material_type: str
    color_hex: str | None
    tool_key: str | None


@dataclass(frozen=True)
class FitResult:
    fit: FeedFit
    short_types: tuple[str, ...]
    color_advisories: tuple[str, ...]


def normalise(value: str) -> str:
    return value.strip().casefold()


def _advisory(need: Need, feed: Feed) -> str | None:
    if need.color_hex and feed.color_hex and need.color_hex != feed.color_hex:
        return f"tool{need.tool_index}:{need.color_hex}->{feed.color_hex}"
    return None


def fit_requirements(needs: list[Need], feeds: list[Feed]) -> FitResult:
    """Place every need on a distinct feed of the same material."""
    ordered = sorted(needs, key=lambda need: need.tool_index)
    tool_keys = {feed.tool_key for feed in feeds if feed.tool_key}
    if len(tool_keys) > 1:
        return _fit_by_tool(ordered, feeds, tool_keys)
    return _fit_from_pool(ordered, feeds)


def _fit_from_pool(needs: list[Need], feeds: list[Feed]) -> FitResult:
    free = list(feeds)
    placed: dict[int, Feed] = {}
    # Same colour first, so a loose type match never takes the feed another
    # requirement matches exactly.
    for exact in (True, False):
        for need in needs:
            if need.tool_index in placed:
                continue
            for feed in free:
                if normalise(feed.material_type) != normalise(need.material_type):
                    continue
                if exact and (not need.color_hex or feed.color_hex != need.color_hex):
                    continue
                placed[need.tool_index] = feed
                free.remove(feed)
                break
    short = sorted(
        {
            normalise(need.material_type)
            for need in needs
            if need.tool_index not in placed
        }
    )
    advisories = sorted(
        advisory
        for need in needs
        if need.tool_index in placed
        and (advisory := _advisory(need, placed[need.tool_index]))
    )
    return FitResult(
        FeedFit.SHORT if short else FeedFit.FITS, tuple(short), tuple(advisories)
    )


def _fit_by_tool(
    needs: list[Need], feeds: list[Feed], tool_keys: set[str]
) -> FitResult:
    short: set[str] = set()
    advisories: set[str] = set()
    unmapped = False
    for need in needs:
        key = f"tool{need.tool_index}"
        if key not in tool_keys:
            unmapped = True
            continue
        on_tool = [feed for feed in feeds if feed.tool_key == key]
        matching = [
            feed
            for feed in on_tool
            if normalise(feed.material_type) == normalise(need.material_type)
        ]
        if not matching:
            short.add(normalise(need.material_type))
            continue
        exact = [feed for feed in matching if feed.color_hex == need.color_hex]
        if advisory := _advisory(need, (exact or matching)[0]):
            advisories.add(advisory)
    fit = FeedFit.SHORT if short else FeedFit.UNMAPPED if unmapped else FeedFit.FITS
    return FitResult(fit, tuple(sorted(short)), tuple(sorted(advisories)))
