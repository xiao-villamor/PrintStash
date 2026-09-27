"""Coverage cannot rot, and it cannot rot in one corner while the total looks fine.

`--cov-fail-under` checks one number over 21,000 statements, and at that size the
aggregate is a blunt instrument: a 900-line service can fall from 95% to 70% and
move the total by half a percent. Every module here that sits below 90% got there
that way — nobody decided to leave `source_covers.py` at 68%, it drifted while the
aggregate stayed green. So the gate is two things: a fixed 90% aggregate minimum,
and a floor every module has to clear on its own.

The measurement is **statements plus branches** (`percent_covered` in
`coverage.json`, with `branch = true` in `pyproject.toml`). That matters: under
line coverage alone an `if x:` whose false path never runs counts as covered, so
this codebase reads as 95.07% by lines and 93.35% once branches are counted. The
second number is the one that says something about the tests.

`PINNED_BELOW_FLOOR` is a debt list, and `MAX_PINNED` prevents new debt. A
coverage improvement is reported for maintenance but never fails a release.

Goes red when: aggregate coverage falls below 90%; a new module lands under the
module floor; or a pinned module falls below its existing pin.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.coverage_gate

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPORT = BACKEND_ROOT / "coverage.json"
LANE = "./scripts/test.sh coverage"

# The aggregate is a fixed release minimum. Per-module floors below remain the
# ratchet that prevents one service from losing coverage behind a healthy total.
TOTAL_FLOOR = 90.0

# What every module must clear on its own.
MODULE_FLOOR = 90.0

# Modules that do not clear it yet, pinned at what they measure today so they
# cannot slide further. Each value is rounded down to the nearest 0.5 from the
# measured figure: the suite runs the app's worker threads, and a pin sitting
# exactly on the measurement would flake on the branches those threads reach.
#
# Deleting an entry is the goal. Improvements are advisory, not failures.
PINNED_BELOW_FLOOR = {
    "app/modules/library/source_covers.py": 76.5,
    "app/modules/ingestion/staging_leases.py": 79.0,
    "app/modules/ingestion/inbox.py": 84.0,
    "app/modules/library/provenance.py": 81.5,
    "app/modules/ingestion/library_transfer.py": 84.0,
    "app/modules/ingestion/ingestion.py": 85.5,
    "app/modules/ingestion/capture_provider_connections.py": 86.5,
    "app/modules/identity/ws_tickets.py": 87.0,
}

# Report a stale pin after a meaningful improvement without blocking the run.
PIN_SLACK = 3.0

# New debt cannot be added without intentionally changing this cap.
MAX_PINNED = 8


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

    def test_reports_improved_pins(self) -> None:
        """Surface stale debt during maintenance without rejecting better tests."""
        measured = _measured()

        drifted = {
            path: measured[path]
            for path, pin in PINNED_BELOW_FLOOR.items()
            if path in measured and measured[path] >= pin + PIN_SLACK
        }
        if drifted:
            warnings.warn(
                "coverage pins can be raised during maintenance: "
                + ", ".join(
                    f"{path} ({percent:.2f}% vs pin {PINNED_BELOW_FLOOR[path]}%)"
                    for path, percent in sorted(drifted.items())
                ),
                UserWarning,
                stacklevel=1,
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

    def test_reports_recovered_modules(self) -> None:
        measured = _measured()

        cleared = {
            path: measured[path]
            for path in PINNED_BELOW_FLOOR
            if path in measured and measured[path] >= MODULE_FLOOR
        }
        if cleared:
            warnings.warn(
                "coverage pins can be removed during maintenance: "
                + ", ".join(
                    f"{path} ({percent:.2f}%)"
                    for path, percent in sorted(cleared.items())
                ),
                UserWarning,
                stacklevel=1,
            )

    def test_the_debt_list_does_not_grow(self) -> None:
        assert len(PINNED_BELOW_FLOOR) <= MAX_PINNED, (
            f"{len(PINNED_BELOW_FLOOR)} pinned modules against a cap of {MAX_PINNED}. "
            "A new entry is a module allowed under the floor forever; write the "
            "tests instead."
        )

    def test_reports_debt_cap_headroom(self) -> None:
        if len(PINNED_BELOW_FLOOR) < MAX_PINNED:
            warnings.warn(
                f"coverage debt cap can be lowered from {MAX_PINNED} to "
                f"{len(PINNED_BELOW_FLOOR)} during maintenance",
                UserWarning,
                stacklevel=1,
            )
