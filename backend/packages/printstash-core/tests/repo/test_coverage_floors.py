"""This package's coverage cannot rot, and no single module can rot inside it.

`printstash_core` is the parsing and protocol layer — G-code, bgcode, meshes, four
printer wire formats, URL safety — so it is almost entirely branches over
malformed input. Line coverage is close to worthless here: a parser can have every
line executed by one well-formed file while the malformed side of every guard is
untried. Hence `branch = true` in `pyproject.toml` and the floors below, which are
statements *and* branches.

The floor is high because this package earns it — 1,444 tests against 3,346
statements, no database, no sockets, nothing to stand in for. A module that cannot
reach `MODULE_FLOOR` here is a module whose error paths were never written down.

Goes red when: coverage fell; a module dropped below the floor; or the suite
improved and the floor was not raised behind it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.coverage_gate

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPORT = PACKAGE_ROOT / "coverage.json"
LANE = "./scripts/test.sh coverage"

# Two-sided: may not fall, and must be raised once the suite clears it by the slack.
TOTAL_FLOOR = 98.8
TOTAL_SLACK = 0.25

# Every module on its own. There is no debt list: nothing in this package is below
# it, and the day something is, the answer is a test rather than an exception.
MODULE_FLOOR = 96.0


def _report() -> dict:
    """The report the coverage lane wrote, or a failure that says how to get one."""
    if not REPORT.exists():
        pytest.fail(
            f"{REPORT.name} does not exist, so there is nothing to check coverage "
            f"against. Run `{LANE}` from {PACKAGE_ROOT.name}/ — it measures the "
            "suite and then runs this file against the report."
        )
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _measured() -> dict[str, float]:
    """Combined statement+branch coverage per module, keyed by package-relative path."""
    return {
        path: data["summary"]["percent_covered"]
        for path, data in _report()["files"].items()
        if data["summary"]["num_statements"] + data["summary"]["num_branches"] > 0
    }


class TestReport:
    def test_measures_branches(self) -> None:
        meta = _report()["meta"]

        assert meta["branch_coverage"] is True, (
            "the report was written without branch coverage, so its numbers are "
            "line-only and the floors here mean nothing against them. `branch = "
            "true` lives in [tool.coverage.run] in pyproject.toml."
        )

    def test_covers_the_whole_package(self) -> None:
        reported = set(_report()["files"])

        shipped = {
            str(path.relative_to(PACKAGE_ROOT))
            for path in (PACKAGE_ROOT / "src" / "printstash_core").rglob("*.py")
        }
        unmeasured = sorted(shipped - reported)
        assert not unmeasured, (
            "these modules ship but appear in no coverage report, so no test imports "
            "them and their real coverage is 0%, not the 100% an absent row reads "
            "as: " + ", ".join(unmeasured)
        )


class TestAggregateFloor:
    def test_total_coverage_holds_its_floor(self) -> None:
        total = _report()["totals"]["percent_covered"]

        assert total >= TOTAL_FLOOR, (
            f"total coverage is {total:.2f}%, below the {TOTAL_FLOOR}% floor. The "
            "term-missing output above names the uncovered lines and the partial "
            "branches, written as `142->exit` or `146->136` — each is an input this "
            "package accepts and no test supplies."
        )

    def test_total_coverage_floor_tracks_the_suite(self) -> None:
        total = _report()["totals"]["percent_covered"]

        assert total < TOTAL_FLOOR + TOTAL_SLACK, (
            f"total coverage is now {total:.2f}%. Raise TOTAL_FLOOR to "
            f"{total - 0.05:.2f} so the gain is locked in."
        )


class TestModuleFloor:
    def test_every_module_clears_the_floor(self) -> None:
        measured = _measured()

        below = {
            path: percent
            for path, percent in measured.items()
            if percent < MODULE_FLOOR
        }
        assert not below, (
            f"these modules are under the {MODULE_FLOOR}% floor: "
            + ", ".join(
                f"{path} ({percent:.2f}%)" for path, percent in sorted(below.items())
            )
            + ". This package has no database and no network to stand in for, so "
            "there is no input it cannot be handed directly — cover the branch."
        )
