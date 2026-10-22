"""Distributed coverage preserves the union of branch evidence across checkouts.

The combined gate must fail when raw data is absent, and must merge executed
branches rather than average the percentages of independently measured shards.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.paths import REPO_ROOT


@pytest.fixture
def coverage_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "combined"
    workspace.mkdir()
    (workspace / ".coverage-inputs").mkdir()
    (workspace / "app").mkdir()
    (workspace / "app/__init__.py").write_text("")
    (workspace / "app/probe.py").write_text(
        "def choose(value):\n    if value:\n        return 1\n    return 0\n"
    )
    shutil.copyfile(REPO_ROOT / "backend/pyproject.toml", workspace / "pyproject.toml")
    return workspace


def _combine(workspace: Path) -> subprocess.CompletedProcess[str]:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deep-ci.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["backend-coverage"]["steps"]
        if step.get("name") == "Combine backend coverage"
    )
    # Substitute only the uv launcher; execute the actual workflow shell command
    # with the real coverage CLI from this test environment.
    launcher = workspace / "uv"
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "assert sys.argv[1] == 'run'\n"
        "os.execv(sys.executable, [sys.executable, '-m', *sys.argv[2:]])\n"
    )
    launcher.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != "COVERAGE_FILE"}
    env["PATH"] = f"{workspace}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=workspace,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class TestBackendCoverageMerge:
    def test_unions_branches_measured_in_different_checkouts(
        self, coverage_workspace: Path, tmp_path: Path
    ) -> None:
        for index in (0, 1):
            checkout = tmp_path / f"runner-{index}"
            checkout.mkdir()
            shutil.copytree(coverage_workspace / "app", checkout / "app")
            shutil.copyfile(
                coverage_workspace / "pyproject.toml", checkout / "pyproject.toml"
            )
            (checkout / "runner.py").write_text(
                "import sys\nfrom app.probe import choose\n"
                "assert choose(int(sys.argv[1])) == int(sys.argv[1])\n"
            )
            subprocess.run(
                [sys.executable, "-m", "coverage", "run", "runner.py", str(index)],
                cwd=checkout,
                env={**os.environ, "COVERAGE_FILE": f".coverage.shard-{index}"},
                capture_output=True,
                text=True,
                check=True,
            )
            data = list(checkout.glob(f".coverage.shard-{index}.*"))
            assert data
            for path in data:
                shutil.copyfile(
                    path, coverage_workspace / ".coverage-inputs" / path.name
                )

        combined = _combine(coverage_workspace)
        assert combined.returncode == 0, combined.stdout + combined.stderr
        subprocess.run(
            [sys.executable, "-m", "coverage", "json"],
            cwd=coverage_workspace,
            env={
                key: value
                for key, value in os.environ.items()
                if key != "COVERAGE_FILE"
            },
            capture_output=True,
            text=True,
            check=True,
        )
        report = json.loads((coverage_workspace / "coverage.json").read_text())
        assert report["meta"]["branch_coverage"] is True
        assert set(report["files"]) == {"app/__init__.py", "app/probe.py"}
        summary = report["files"]["app/probe.py"]["summary"]
        assert summary["num_branches"] == summary["covered_branches"] == 2
        assert summary["missing_lines"] == summary["missing_branches"] == 0
        assert summary["percent_covered"] == 100

    def test_rejects_missing_shard_data(self, coverage_workspace: Path) -> None:
        combined = _combine(coverage_workspace)

        assert combined.returncode != 0
        assert "No data to combine" in combined.stdout + combined.stderr
        assert not (coverage_workspace / "coverage.json").exists()
