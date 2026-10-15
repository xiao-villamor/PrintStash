"""A resource check judges the tests a run selected, by their real markers.

`tests/conftest.py` stops a session that selected a `postgres`, `s3`,
`remote_storage` or `bgcode` test while the service behind it cannot start: an
error, never a skip. Two mistakes made that stop fire on runs that selected none
of them. It ran before `-m`, `-k` and `--deselect` removed anything, and it
matched `item.keywords`, where a parameter id such as `[postgres]` counts as much
as a marker. So `./scripts/test.sh fast` demanded a daemon on any machine without
one, and under xdist every worker exited without the controller saying why
("Unexpectedly no active workers available").

Each case runs pytest in a throwaway project that loads the suite's own hooks.
A stub stands in for the daemon, so no case needs Docker or reaches it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import conftest
from tests.paths import REPO_ROOT

RESOURCES = sorted(conftest._RESOURCES.items())
# Ids that are not marker names, so this file's own items never look marked.
RESOURCE_CASES = [
    pytest.param(marker, resource, id=f"{marker}-marked")
    for marker, resource in RESOURCES
]

MARKED_TEST = (
    "import pytest\n\n@pytest.mark.postgres\ndef test_needs_a_service():\n    pass\n"
)
UNMARKED_TEST = "def test_needs_nothing():\n    pass\n"
STOP = "cannot start PostgreSQL"


def _project(root: Path, files: dict[str, str], *, docker: bool = False) -> Path:
    """A pytest project whose conftest is the suite's hooks over a stubbed daemon."""
    markers = "".join(
        f"    {name}: registered for the throwaway project\n"
        for name in ("contract", "e2e", *conftest._RESOURCES)
    )
    (root / "pytest.ini").write_text(f"[pytest]\naddopts =\nmarkers =\n{markers}")
    (root / "conftest.py").write_text(
        "from tests import conftest as suite\n"
        "from tests import containers\n"
        "\n"
        "globals().update(\n"
        "    {\n"
        "        name: hook\n"
        "        for name, hook in vars(suite).items()\n"
        "        if name.startswith('pytest_') and callable(hook)\n"
        "    }\n"
        ")\n"
        f"containers.docker_available = lambda: {docker}\n"
    )
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return root


def _pytest(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run pytest in *project* with nothing inherited from the outer session."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("PYTEST_")
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=project,
        env={
            **environment,
            "PYTHONPATH": str(REPO_ROOT / "backend"),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


class TestPytestCollectionModifyitems:
    def test_lets_marker_selection_reach_a_postgres_directory_test(
        self, tmp_path: Path
    ) -> None:
        project = _project(
            tmp_path, {"postgres/test_dialect.py": UNMARKED_TEST}, docker=True
        )

        result = _pytest(project, "-q", "-m", "postgres")

        assert "1 passed" in result.stdout, result.stdout + result.stderr

    def test_subjects_a_postgres_directory_test_to_the_resource_check(
        self, tmp_path: Path
    ) -> None:
        project = _project(tmp_path, {"postgres/test_dialect.py": UNMARKED_TEST})

        result = _pytest(project, "-q")

        assert STOP in result.stdout + result.stderr, result.stdout + result.stderr


class TestPytestCollectionFinish:
    def test_runs_a_marked_test_when_its_service_can_start(
        self, tmp_path: Path
    ) -> None:
        project = _project(tmp_path, {"test_marked.py": MARKED_TEST}, docker=True)

        result = _pytest(project, "-q")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 passed" in result.stdout

    def test_runs_tests_whose_parameter_ids_name_a_resource(
        self, tmp_path: Path
    ) -> None:
        ids = [marker for marker, _ in RESOURCES]
        project = _project(
            tmp_path,
            {
                "test_ids.py": (
                    "import pytest\n\n"
                    f"@pytest.mark.parametrize('variant', {ids!r}, ids=str)\n"
                    "def test_variant(variant):\n"
                    "    pass\n"
                )
            },
        )

        result = _pytest(project, "-q")

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"{len(ids)} passed" in result.stdout

    @pytest.mark.parametrize(
        "selection",
        [
            pytest.param(("-m", "not postgres"), id="marker-expression"),
            pytest.param(("-k", "not needs_a_service"), id="keyword-expression"),
            pytest.param(
                ("--deselect", "test_marked.py::test_needs_a_service"), id="deselect"
            ),
        ],
    )
    def test_runs_without_the_service_once_its_tests_are_deselected(
        self, tmp_path: Path, selection: tuple[str, str]
    ) -> None:
        project = _project(
            tmp_path, {"test_marked.py": MARKED_TEST, "test_plain.py": UNMARKED_TEST}
        )

        result = _pytest(project, "-q", *selection)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 passed, 1 deselected" in result.stdout

    def test_lists_a_marked_test_under_collect_only(self, tmp_path: Path) -> None:
        project = _project(tmp_path, {"test_marked.py": MARKED_TEST})

        result = _pytest(project, "-q", "--collect-only")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "test_marked.py::test_needs_a_service" in result.stdout

    def test_keeps_the_stop_another_plugin_requested(self, tmp_path: Path) -> None:
        project = _project(
            tmp_path,
            {
                "test_marked.py": MARKED_TEST,
                "early_stop.py": (
                    "def pytest_collection_modifyitems(session):\n"
                    "    session.shouldstop = 'stopped by another plugin'\n"
                ),
            },
        )

        result = _pytest(project, "-q", "-p", "early_stop")

        assert "stopped by another plugin" in result.stdout, (
            result.stdout + result.stderr
        )
        assert STOP not in result.stdout + result.stderr

    @pytest.mark.parametrize(("marker", "resource"), RESOURCE_CASES)
    def test_stops_before_a_selected_marked_test_runs(
        self, tmp_path: Path, marker: str, resource: str
    ) -> None:
        project = _project(
            tmp_path,
            {
                "test_marked.py": (
                    "import pytest\n\n"
                    f"@pytest.mark.{marker}\n"
                    "def test_needs_a_service():\n"
                    "    pass\n"
                )
            },
        )

        result = _pytest(project, "-q")

        assert result.returncode == 1, result.stdout + result.stderr
        assert f"cannot start {resource}" in result.stdout + result.stderr
        assert "passed" not in result.stdout

    def test_reports_the_stop_through_the_xdist_controller(
        self, tmp_path: Path
    ) -> None:
        project = _project(
            tmp_path, {"test_marked.py": MARKED_TEST, "test_plain.py": UNMARKED_TEST}
        )

        result = _pytest(project, "-q", "-p", "xdist.plugin", "-n", "2")

        assert STOP in result.stdout + result.stderr, result.stdout + result.stderr
        assert "no active workers" not in result.stdout + result.stderr

    def test_reports_an_earlier_collection_error_instead_of_the_stop(
        self, tmp_path: Path
    ) -> None:
        project = _project(
            tmp_path,
            {
                "test_marked.py": MARKED_TEST,
                "test_broken.py": "raise RuntimeError('broken at import')\n",
            },
        )

        result = _pytest(project, "-q")

        assert "1 error during collection" in result.stdout, (
            result.stdout + result.stderr
        )
        assert STOP not in result.stdout + result.stderr
