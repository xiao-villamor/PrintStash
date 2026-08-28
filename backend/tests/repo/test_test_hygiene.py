"""Invariants that stop the suite's own structure decaying again.

This suite reached 347 module-level `_helper` functions before anyone counted.
`_user` existed thirteen times and disagreed with itself about whether its default
was a superuser; `make_model` existed twice with incompatible argument orders;
four test files imported private helpers out of *other test files*. None of that
was one bad decision — it was the same reasonable local decision made
independently many times, which is exactly the kind of drift a review does not
catch and a test does.

So each rule here is a habit that has already cost this repo real debugging time,
turned into something that fails loudly instead:

* No test module imports another test module. That coupling meant deleting a
  helper broke collection in an unrelated directory.
* No test builds a `User`, `Model`, `File` or `Printer` row by hand. The
  builders encode which columns silently mislead — a hand-built row that gets one
  wrong inserts cleanly and is then invisible to the code under test, so the test
  passes against nothing.
* No two files define a row builder with the same name. That is the divergence
  that made the identical call mean different things in different files.
* Every test module is named for the production module it defends. A test named
  for a *topic* — `test_staging_lease_ownership`, `test_provenance_helpers`,
  `test_storage_ownership_quarantine` — cannot be found from the module it
  covers, so "is this module tested?" stops being one `ls` and becomes a search.
  That is what makes a coverage matrix guesswork: nobody can tell an untested
  module from one whose tests live under a name they did not think of.

Each rule names the fix in its failure message, because the person who trips it is
usually not the person who read the guidance.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]

# Row constructions the factories cover. Anything here built inline is either a
# missed migration or a builder that needs extending.
FACTORY_OWNED_MODELS = {
    "User": "build_user",
    "Model": "build_model",
    "File": "build_file",
    "Printer": "build_printer (or printer_config for an unsaved row)",
    "Collection": "build_collection",
    "PrintJob": "build_print_job",
    "CollectionPermission": "grant_collection_role",
}

# Files that legitimately construct rows directly.
CONSTRUCTION_ALLOWED = {
    "tests/factories",  # the builders themselves
    "tests/integration/_backup_harness.py",  # seeds a separate engine's schema
    "tests/repo",  # these invariants, and the factory tests
}

# Names that read like a row builder. A second definition of one of these in a
# different file is the divergence this rule exists to catch.
BUILDER_NAME = re.compile(
    r"^_(make_|build_)?(user|model|file|artifact|printer|collection|gcode|"
    r"source|library|document|job|item|slot|printer_file|print_job)s?$"
)


# ---------------------------------------------------------------------------
# The remaining ratchet.
#
# `PENDING_INLINE_CONSTRUCTION` is gone: every test file now builds its rows
# through `tests/factories/`, so that rule is absolute rather than aspirational.
# This one is the last of the pair — a handful of local builder *names* still
# shadow a factory, and each one removed narrows the gap. **The list may only
# ever shrink.**
#
# Migrating one is usually mechanical: delete the local builder, call the
# factory, and make any state the local default was hiding explicit at the call
# site. See .agents/skills/create-tests/references/fixtures.md
# ---------------------------------------------------------------------------
PENDING_DUPLICATE_BUILDERS = {
    "_job",
    "_make_file",
    "_make_item",
    "_make_model",
    "_make_user",
    "_model",
    "_printer",
    "_source",
}


CORE_TESTS_ROOT = TESTS_ROOT.parent / "packages" / "printstash-core" / "tests"

# Where a test tree's production modules live. A test under `<tests>/<tier>/<path>`
# mirrors `<source root>/<path>`.
MIRROR_ROOTS = {
    TESTS_ROOT: TESTS_ROOT.parent / "app",
    CORE_TESTS_ROOT: CORE_TESTS_ROOT.parent / "src" / "printstash_core",
}

# The tier directories a mirror is measured under. `e2e/` is deliberately absent:
# its files are named for a *flow* (`test_ingest.py`), which is the whole point of
# that tier — an e2e test crosses every module rather than defending one.
MIRRORED_TIERS = ("unit", "integration", "contract")

# Directories that mirror something that is not a production module, each for a
# stated reason. This is not a backlog: nothing here will ever acquire a mirror.
NON_MIRRORED_DIRS = {
    # Repo-level invariants — this file included.
    "tests/repo": "defends the repository, not a module",
    "packages/printstash-core/tests/repo": "defends the package, not a module",
    # Support code rather than tests.
    "tests/fakes": "emulators and contract fakes",
    "tests/fixtures": "data files",
    "tests/factories": "row builders",
    # The migration chain lives in `alembic/versions/`, not in `app/`, and its
    # tests are named for the migration or the invariant they exercise.
    "tests/integration/db/migrations": "mirrors the alembic chain",
    # A dialect is not a module. These re-run cross-cutting behaviour against a
    # real PostgreSQL, gated by the `postgres` marker.
    "tests/integration/postgres": "mirrors a dialect, not a module",
    # `printstash_core_testkit` is a sibling top-level package under the same
    # `src/`; naming the directory after it would not make the mirror any clearer.
    "packages/printstash-core/tests/testkit": "mirrors the sibling testkit package",
}

# The last genuine violations. **This list may only shrink.** Each entry is a file
# whose name describes a topic rather than the module it defends; the fix is to
# rename it, or to move it into a folder named for that module when one file would
# be too long.
#
# It is empty, and the two assertions below are what keep it that way: one fails on
# a new violation, the other on an entry that is no longer one.
PENDING_UNMIRRORED_MODULES: set[str] = set()


def _test_modules() -> list[Path]:
    return sorted(
        path
        for path in TESTS_ROOT.rglob("test_*.py")
        if "__pycache__" not in path.parts
    )


def _all_test_modules() -> list[Path]:
    """Every pytest module in the repo, `printstash-core`'s included.

    The rules below split into two sets, and the split is not arbitrary. The
    factory rules are backend-only: `printstash-core` has no database and no
    `tests/factories`, so applying them there would flag class names that merely
    collide with an app model. Everything about *shape* — a contract header, a
    group per unit, a name that names one behaviour — applies to both trees,
    because both are read by the same people for the same reasons.
    """
    return sorted(
        [*_test_modules()]
        + [
            path
            for path in CORE_TESTS_ROOT.rglob("test_*.py")
            if "__pycache__" not in path.parts
        ]
    )


def _relative(path: Path) -> str:
    return str(path.relative_to(TESTS_ROOT.parent))


def _factory_checked_modules() -> list[Path]:
    """Test modules the factory rule applies to.

    Filtered rather than skipped. An entry in `CONSTRUCTION_ALLOWED` is a file the
    rule does not apply to — the builders themselves, the backup harness that seeds a
    separate engine's schema — so generating a case for it and skipping reports
    thirteen declined tests where there are none. A skip should mean "this could pass
    and did not run", and none of these ever could.
    """
    return [module for module in _all_test_modules() if not _is_allowed(module)]


def _is_allowed(path: Path) -> bool:
    return any(fragment in _relative(path) for fragment in CONSTRUCTION_ALLOWED)


def _mirror_of(module: Path) -> Path | None:
    """The production module this test file defends, or None when nothing matches.

    Four shapes count, and each exists for a reason:

    * `<tier>/<pkg>/test_<mod>.py` → `<src>/<pkg>/<mod>.py` — the ordinary case.
    * `<tier>/<pkg>/test_<mod>.py` → `<src>/<pkg>/<mod>/__init__.py` — a module
      implemented as a package.
    * `<tier>/<pkg>/<mod>/test_<group>.py` → `<src>/<pkg>/<mod>.py` — the split a
      module too large for one test file gets. The *directory* is the mirror; the
      file names inside it are endpoint or method groups.
    * `<tier>/<pkg>/test_<pkg>.py` → `<src>/<pkg>/__init__.py` — a package whose
      code lives in its own `__init__`, named for itself.
    """
    for tests_root, source_root in MIRROR_ROOTS.items():
        if not module.is_relative_to(tests_root):
            continue
        relative = module.relative_to(tests_root)
        if tests_root is TESTS_ROOT:
            if relative.parts[0] not in MIRRORED_TIERS:
                return None
            relative = Path(*relative.parts[1:])
        stem = relative.name.removeprefix("test_").removesuffix(".py")
        candidates = [
            source_root / relative.parent / f"{stem}.py",
            source_root / relative.parent / stem / "__init__.py",
            source_root / relative.parent.with_suffix(".py"),
        ]
        if stem == relative.parent.name:
            candidates.append(source_root / relative.parent / "__init__.py")
        return next((path for path in candidates if path.exists()), None)
    return None


def _mirror_checked_modules() -> list[Path]:
    """Test modules the mirror rule applies to.

    Filtered rather than skipped, for the same reason as the factory rule: an entry
    in `NON_MIRRORED_DIRS` is a file the rule does not apply to, and reporting it as
    a skip would claim a test declined to run when none ever could. The
    `PENDING_UNMIRRORED_MODULES` entries are filtered here for the same reason — a
    recorded violation is tracked by the staleness test below, not by a skip.
    """
    checked = []
    for module in _all_test_modules():
        relative = _relative(module)
        if relative in PENDING_UNMIRRORED_MODULES:
            continue
        if any(relative.startswith(f"{directory}/") for directory in NON_MIRRORED_DIRS):
            continue
        if module.is_relative_to(TESTS_ROOT) and (
            relative.split("/")[1] not in MIRRORED_TIERS
        ):
            continue
        checked.append(module)
    return checked


class TestSuiteHygiene:
    @pytest.mark.parametrize("module", _all_test_modules(), ids=_relative)
    def test_every_test_belongs_to_a_group(self, module: Path) -> None:
        """No test is defined at module level. Every one lives in a `class Test*`.

        The group names the production unit its tests exercise, which is what
        turns "what covers `scan_library`?" from a grep into a lookup — and it is
        the only reason a 900-line file is navigable at all. A test at module
        level belongs to nothing, so it accumulates in whatever order it was
        written and drifts away from the code it defends.

        This is absolute rather than a ratchet: the whole suite was converted, so
        the first module-level test to reappear is the regression.
        """
        offenders = [
            node.name
            for node in ast.parse(module.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]

        assert not offenders, (
            f"{_relative(module)} defines {len(offenders)} test(s) at module "
            f"level: {', '.join(offenders[:5])}. Move each into the "
            "`class Test<Unit>` for the production unit it exercises, in that "
            "module's own order. See .agents/skills/create-tests/SKILL.md"
        )

    @pytest.mark.parametrize("module", _all_test_modules(), ids=_relative)
    def test_every_file_opens_with_a_contract_header(self, module: Path) -> None:
        """A test file says what it defends, in prose, before its first import.

        Not a restatement of the filename. The header is where the *reason* a rule
        exists lives — and that reason is the thing a reader needs when the file goes
        red six months from now and the obvious fix is to delete the assertion.
        """
        header = ast.get_docstring(ast.parse(module.read_text(encoding="utf-8")))

        assert header, (
            f"{_relative(module)} has no module docstring. Open it with a few lines "
            "on what this file defends and why it matters when it goes red — see "
            "Inside a test file in .agents/skills/create-tests/SKILL.md"
        )

    @pytest.mark.parametrize("module", _mirror_checked_modules(), ids=_relative)
    def test_every_module_is_named_for_the_module_it_defends(
        self, module: Path
    ) -> None:
        """A test file is found by translating a production path, never by guessing.

        `test_staging_lease_ownership.py`, `test_provenance_helpers.py` and
        `test_storage_ownership_quarantine.py` each defended a real service and none
        of them could be found from it. That is the failure this catches: an audit of
        `app/services/trash.py` has to be an audit of one file, and "does this module
        have tests?" has to be one `ls`, or the coverage matrix is guesswork.

        When one file would be too long, the answer is a *folder* named for the
        module — `integration/services/storage_backend/{test_objects,test_ownership}.py`
        — not a second file named for a topic.
        """
        assert _mirror_of(module) is not None, (
            f"{_relative(module)} names no production module. Rename it after the "
            "module it defends, or move it into a folder named for that module "
            "when one file would be too long. If it defends the repository rather "
            "than a module, it belongs in tests/repo/ — see "
            "Where tests live in .agents/skills/create-tests/SKILL.md"
        )

    def test_the_unmirrored_list_has_no_stale_entries(self) -> None:
        """A file that acquired its mirror leaves the list in the same commit.

        A *deleted* entry has to leave too, and needs its own check: the mirror
        lookup answers `None` for a path that no longer exists, which is the same
        answer it gives a genuine violation. Without this the list would go on
        claiming a violation for a file nobody can open.
        """
        stale = sorted(
            entry
            for entry in PENDING_UNMIRRORED_MODULES
            if (path := TESTS_ROOT.parent / entry) is not None
            and (not path.exists() or _mirror_of(path) is not None)
        )

        assert not stale, (
            f"no longer violations: {stale}. Remove them from "
            "PENDING_UNMIRRORED_MODULES so the list keeps meaning something."
        )

    @pytest.mark.parametrize("module", _test_modules(), ids=_relative)
    def test_no_test_module_imports_another_test_module(self, module: Path) -> None:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        offenders = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("tests.")
            and ".test_" in f".{node.module.rsplit('.', 1)[-1]}"
        ]

        assert not offenders, (
            f"{_relative(module)} imports from another test module: {offenders}. "
            "A test module is not an API. Move the shared thing into "
            "tests/factories/ (rows), tests/_env.py (environment), or the nearest "
            "conftest.py (fixtures) — deleting a helper must not break collection "
            "in another directory."
        )

    @pytest.mark.parametrize("module", _factory_checked_modules(), ids=_relative)
    def test_rows_are_built_through_the_factories(self, module: Path) -> None:
        """No test file builds a factory-owned row by hand. Every file, no exemptions.

        This used to carry a per-file exemption list while the migration ran; it does
        not any more, and that is the point of keeping the docstring here. The reason
        the rule is absolute is that an inline row fails *silently*: `deleted_at=`
        instead of `trashed=` produces a row every read path filters out, and a
        printer missing three of its provider's four credential fields inserts
        happily and then fails somewhere unrelated. Neither looks like a setup bug.

        If a file genuinely cannot use a builder, the answer is a factory that covers
        its case — `printer_config` and the `detached_*` helpers exist because of
        exactly that — or an entry in `CONSTRUCTION_ALLOWED` with a reason, which
        removes the file from `_factory_checked_modules()` rather than skipping it.
        """
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        offenders: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in FACTORY_OWNED_MODELS
                # A bare `Model()` with no arguments is a sentinel or a type probe,
                # not a row somebody meant to persist.
                and (node.args or node.keywords)
            ):
                offenders.add(node.func.id)

        assert not offenders, (
            f"{_relative(module)} constructs "
            + ", ".join(
                f"{name}() (use {FACTORY_OWNED_MODELS[name]})"
                for name in sorted(offenders)
            )
            + ". The builders encode which columns silently mislead — `trashed=` "
            "rather than `deleted_at`, `provider=` rather than four credential "
            "fields — and a row that gets one wrong is invisible to the code under "
            "test rather than an error. See "
            ".agents/skills/create-tests/references/fixtures.md"
        )

    def test_no_new_duplicate_row_builder_names_appear(self) -> None:
        """Two files defining the same builder name is how `_user` drifted.

        Thirteen copies, and two different defaults for `superuser`, so the identical
        call meant opposite things depending on which file you were reading. The
        remaining pairs are listed in `PENDING_DUPLICATE_BUILDERS` and that list may
        only shrink — a *new* duplicate name fails here immediately.
        """
        definitions: dict[str, list[str]] = {}
        for module in _test_modules():
            if _is_allowed(module):
                continue
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and BUILDER_NAME.match(node.name):
                    definitions.setdefault(node.name, []).append(_relative(module))

        duplicated = {
            name for name, files in definitions.items() if len(files) > 1
        } - PENDING_DUPLICATE_BUILDERS

        assert not duplicated, (
            f"new duplicate row-builder name(s): {sorted(duplicated)}. Promote the "
            "builder to tests/factories/ rather than defining it a second time — see "
            ".agents/skills/create-tests/references/fixtures.md"
        )

    def test_the_duplicate_builder_list_has_no_stale_entries(self) -> None:
        """A name that is no longer duplicated must leave the list in the same commit."""
        definitions: dict[str, int] = {}
        for module in _test_modules():
            if _is_allowed(module):
                continue
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and BUILDER_NAME.match(node.name):
                    definitions[node.name] = definitions.get(node.name, 0) + 1

        resolved = sorted(
            name for name in PENDING_DUPLICATE_BUILDERS if definitions.get(name, 0) <= 1
        )

        assert not resolved, (
            f"no longer duplicated: {resolved}. Remove them from "
            "PENDING_DUPLICATE_BUILDERS so the list keeps meaning something."
        )

    def test_no_test_name_joins_two_behaviours(self) -> None:
        """No test name contains `_and_`. The rule is absolute; there is no cap.

        It was a ratchet at 313 while the backlog came down, and the backlog is
        gone, so the exception went with it. Two shapes of offender turned up, and
        both are worth keeping out:

        A name joining two behaviours is two tests. When it fails, the failure
        cannot say which half broke — and splitting them repeatedly turned up
        assertions that were passing for the wrong reason, because one half had
        silently set up the other. `progress_is_monotonic_below_terminal_and_
        completed_forces_100` asserted a lower clamp that does not exist: the 99.0
        it checked was the *earlier* value being kept, and a fresh job handed -3.0
        reports 0.0.

        A name listing the assertions of a single behaviour is just a worse name.
        `gc_hard_deletes_expired_artifact_and_its_derivatives` is one behaviour —
        the derivatives are part of what "hard delete" means.

        The one honest false positive is a name that embeds a production symbol
        containing "and", like `_download_and_collect`. The unit's name belongs to
        the `class Test…` group, not to the test, so those became behaviour names
        and the rule stayed absolute.
        """
        offenders = []
        for module in _all_test_modules():
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test_")
                    and "_and_" in node.name
                ):
                    offenders.append(f"{_relative(module)}::{node.name}")

        assert not offenders, (
            "these test names contain `_and_`. If the name joins two behaviours, "
            "split it so a failure says which half broke; if it lists the "
            "assertions of one behaviour, name the behaviour instead. If the `and` "
            "is part of a production symbol, the `class Test…` group already "
            "carries the unit's name.\n  " + "\n  ".join(sorted(offenders))
        )


class TestSkips:
    """No test in this suite skips itself.

    A skip is a run that reports success having verified nothing, and there are only
    two honest reasons to write one: a resource is missing, or the case does not
    apply. This suite answers both differently.

    **A missing resource is an error.** `tests/containers.py` starts PostgreSQL and
    SeaweedFS itself and stops the session with a message when Docker is not there,
    rather than skipping 26 tests and reporting green. `psutil` replaced
    `/proc/self/status` so the memory-reclamation assertions run on macOS too, and the
    mesh corpus defaults to `testdata/` instead of waiting for an environment variable
    nobody sets.

    **A case that does not apply is not generated.** `test_transport_errors_become_
    provider_errors` is parametrized over the providers that *have* an injectable
    transport, and the factory rule over the files it applies to — filtered lists, not
    `pytest.skip` inside the body. Generating a case and skipping it reports a test
    that was never written as a test that was declined, and the number can never
    become a pass.

    `xfail` is out for the same reason, and `skipif` on a platform check is the shape
    this rule exists to catch: it silently narrows what the maintainer's machine
    verifies.
    """

    @pytest.mark.parametrize("module", _all_test_modules(), ids=_relative)
    def test_no_module_skips_or_xfails(self, module: Path) -> None:
        source = module.read_text(encoding="utf-8")
        if module.name == Path(__file__).name:
            return

        markers = sorted(
            {
                marker
                for marker in (
                    "pytest.skip(",
                    "pytest.xfail(",
                    "mark.skip",
                    "mark.xfail",
                )
                if marker in source
            }
        )

        assert not markers, (
            f"{_relative(module)} uses {', '.join(markers)}. If a resource is missing, "
            "fail with a message that names it — see tests/containers.py. If the case "
            "does not apply, do not generate it: filter the parametrize list instead."
        )
