"""Coverage cannot rot, and it cannot rot in one corner while the total looks fine.

`--cov-fail-under` checks one number over 21,000 statements, and at that size the
aggregate is a blunt instrument: a 900-line service can fall from 95% to 70% and
move the total by half a percent. Every module here that sits below 90% got there
that way — nobody decided to leave `source_covers.py` at 68%, it drifted while the
aggregate stayed green. So the gate is two things: the aggregate, ratcheted in both
directions, and a floor every module has to clear on its own.

The measurement is **statements plus branches** (`percent_covered` in
`coverage.json`, with `branch = true` in `pyproject.toml`). That matters: under
line coverage alone an `if x:` whose false path never runs counts as covered, so
this codebase reads as 95.07% by lines and 93.35% once branches are counted. The
second number is the one that says something about the tests.

`PINNED_BELOW_FLOOR` is a debt list, and `MAX_PINNED` is what makes it one — the
list may only shrink. Raising a pinned module to `MODULE_FLOOR` fails this file
until its entry is deleted and the cap lowered, which is the only direction the
ratchet turns.

Goes red when: coverage fell anywhere; a new module landed under the floor; or a
pinned module improved and the debt list wasn't updated to record it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.coverage_gate

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPORT = BACKEND_ROOT / "coverage.json"
LANE = "./scripts/test.sh coverage"

# The aggregate, statements + branches. Two-sided: it may not fall, and when it
# rises past the slack below it has to be raised here, so the number in this file
# is always roughly what the suite actually achieves rather than a floor from
# eighteen months ago that everything clears by ten points.
TOTAL_FLOOR = 93.56
# 0.25pp of ~27,400 statements+branches is ~70 units of coverage — enough that a
# normal PR adding tests for one behaviour does not force an edit here, small
# enough that a sustained improvement does.
TOTAL_SLACK = 0.25

# What every module must clear on its own.
MODULE_FLOOR = 90.0

# Modules that do not clear it yet, pinned at what they measure today so they
# cannot slide further. Each value is rounded down to the nearest 0.5 from the
# measured figure: the suite runs the app's worker threads, and a pin sitting
# exactly on the measurement would flake on the branches those threads reach.
#
# Deleting an entry is the goal. See the module docstring for the ratchet.
PINNED_BELOW_FLOOR = {
    "app/services/source_covers.py": 76.5,
    "app/services/staging_leases.py": 76.0,
    "app/services/inbox.py": 81.0,
    "app/services/provenance.py": 81.5,
    "app/services/external_library.py": 83.5,
    "app/services/library_transfer.py": 84.0,
    "app/services/ingestion.py": 85.5,
    "app/services/capture_provider_connections.py": 86.5,
    "app/services/ws_tickets.py": 87.0,
    "app/services/library_watcher.py": 87.5,
    "app/services/storage_backend.py": 88.0,
    "app/services/backup.py": 88.5,
    "app/schemas/provenance.py": 89.0,
    "app/services/mesh_processing.py": 89.0,
    "app/services/importer.py": 89.5,
}

# How far a pinned module may rise above its pin before the pin has to move. Wide
# enough that covering one behaviour does not force an edit here, narrow enough that
# a module cannot quietly gain ten points and keep the old floor.
PIN_SLACK = 3.0

# Two-sided, the same shape as the other ratchets in this directory: the list may
# not grow, and when it shrinks this has to come down with it.
MAX_PINNED = 15


def _report() -> dict:
    """The report the coverage lane wrote, or a failure that says how to get one.

    Not a skip. A gate that skips itself when its input is missing reports a green
    run having checked nothing, which is the failure mode this file exists to
    prevent one level down.
    """
    if not REPORT.exists():
        pytest.fail(
            f"{REPORT.relative_to(BACKEND_ROOT)} does not exist, so there is nothing "
            f"to check coverage against. Run `{LANE}` — it measures the suite and "
            "then runs this file against the report."
        )
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _measured() -> dict[str, float]:
    """Combined statement+branch coverage per module, keyed by repo-relative path."""
    files = _report()["files"]
    return {
        path: data["summary"]["percent_covered"]
        for path, data in files.items()
        if data["summary"]["num_statements"] + data["summary"]["num_branches"] > 0
    }


class TestReport:
    def test_measures_branches(self) -> None:
        meta = _report()["meta"]

        assert meta["branch_coverage"] is True, (
            "the report was written without branch coverage, so its numbers are "
            "line-only and every floor in this file is meaningless against them. "
            "`branch = true` lives in [tool.coverage.run] in pyproject.toml; a "
            "`--cov` invocation that overrides it is the likely cause."
        )

    def test_covers_the_whole_application_package(self) -> None:
        # Every path the report knows about, including the ones with nothing to
        # measure (`__init__.py`), so a package marker does not read as a gap.
        reported = set(_report()["files"])

        shipped = {
            str(path.relative_to(BACKEND_ROOT))
            for path in (BACKEND_ROOT / "app").rglob("*.py")
            if path.name != "__main__.py"
        }
        unmeasured = sorted(shipped - reported)
        assert not unmeasured, (
            "these modules ship but appear in no coverage report, which means no "
            "test imports them and their real coverage is 0%, not the 100% an "
            "absent row reads as: " + ", ".join(unmeasured)
        )


class TestAggregateFloor:
    def test_total_coverage_holds_its_floor(self) -> None:
        total = _report()["totals"]["percent_covered"]

        assert total >= TOTAL_FLOOR, (
            f"total coverage is {total:.2f}%, below the {TOTAL_FLOOR}% floor. The "
            "term-missing output above names the uncovered lines and partial "
            "branches; each one is a matrix row that has no test."
        )

    def test_total_coverage_floor_tracks_the_suite(self) -> None:
        total = _report()["totals"]["percent_covered"]

        assert total < TOTAL_FLOOR + TOTAL_SLACK, (
            f"total coverage is now {total:.2f}%, comfortably above the "
            f"{TOTAL_FLOOR}% floor. Raise TOTAL_FLOOR to {total - 0.05:.2f} so the "
            "gain is locked in — a floor nobody moves stops being a gate."
        )


class TestModuleFloor:
    def test_every_unpinned_module_clears_the_floor(self) -> None:
        measured = _measured()

        below = {
            path: percent
            for path, percent in measured.items()
            if percent < MODULE_FLOOR and path not in PINNED_BELOW_FLOOR
        }
        assert not below, (
            f"these modules are under the {MODULE_FLOOR}% floor and are not on the "
            "debt list: "
            + ", ".join(
                f"{path} ({percent:.2f}%)" for path, percent in sorted(below.items())
            )
            + ". Cover the gap rather than pinning it — the debt list is for what "
            "was already there when the floor went in, and it is only allowed to "
            "shrink."
        )

    def test_every_pinned_module_holds_its_pin(self) -> None:
        measured = _measured()

        # Rounded to the report's own precision before comparing. A pin sitting
        # exactly on the measured figure otherwise fails on the float below it —
        # `81.4999…` renders as `81.50` and reads as a regression against `81.5`.
        fallen = {
            path: (measured[path], pin)
            for path, pin in PINNED_BELOW_FLOOR.items()
            if path in measured and round(measured[path], 2) < pin
        }
        assert not fallen, (
            "these modules fell below what they were already pinned at: "
            + ", ".join(
                f"{path} {now:.2f}% < {pin}%"
                for path, (now, pin) in sorted(fallen.items())
            )
        )

    def test_no_pinned_module_has_drifted_far_above_its_pin(self) -> None:
        """A pin that is stale by a wide margin has stopped constraining anything.

        The aggregate floor is two-sided; these were not, and that showed:
        `source_covers.py` went from 68% to 77% in one change and its 68% pin
        happily accepted every point in between. `MODULE_FLOOR` only notices at 90%,
        which is a long way to drift unwatched.
        """
        measured = _measured()

        drifted = {
            path: measured[path]
            for path, pin in PINNED_BELOW_FLOOR.items()
            if path in measured and measured[path] >= pin + PIN_SLACK
        }
        assert not drifted, (
            "these pins are more than "
            f"{PIN_SLACK}pp below what the module now measures: "
            + ", ".join(
                f"{path} ({percent:.2f}% vs pin {PINNED_BELOW_FLOOR[path]}%)"
                for path, percent in sorted(drifted.items())
            )
            + ". Raise each pin to just under its figure, so the ground gained "
            "cannot be given back."
        )


class TestDebtList:
    def test_no_pin_names_a_module_that_is_no_longer_measured(self) -> None:
        measured = _measured()

        stale = sorted(set(PINNED_BELOW_FLOOR) - set(measured))
        assert not stale, (
            "PINNED_BELOW_FLOOR names modules that no longer appear in the report — "
            "deleted, renamed, or no longer imported by any test. Remove or retarget "
            "them: " + ", ".join(stale)
        )

    def test_no_pinned_module_has_already_cleared_the_floor(self) -> None:
        measured = _measured()

        cleared = {
            path: measured[path]
            for path in PINNED_BELOW_FLOOR
            if path in measured and measured[path] >= MODULE_FLOOR
        }
        assert not cleared, (
            "these modules now clear the floor on their own: "
            + ", ".join(
                f"{path} ({percent:.2f}%)" for path, percent in sorted(cleared.items())
            )
            + f". Delete their entries and lower MAX_PINNED to "
            f"{len(PINNED_BELOW_FLOOR) - len(cleared)} — that is the whole point of "
            "the list."
        )

    def test_the_debt_list_does_not_grow(self) -> None:
        assert len(PINNED_BELOW_FLOOR) <= MAX_PINNED, (
            f"{len(PINNED_BELOW_FLOOR)} pinned modules against a cap of {MAX_PINNED}. "
            "A new entry is a module allowed under the floor forever; write the "
            "tests instead."
        )

    def test_the_debt_cap_tracks_the_list(self) -> None:
        assert len(PINNED_BELOW_FLOOR) >= MAX_PINNED, (
            f"the debt list is down to {len(PINNED_BELOW_FLOOR)} entries. Lower "
            f"MAX_PINNED from {MAX_PINNED} so the room that was just freed cannot be "
            "silently refilled."
        )
