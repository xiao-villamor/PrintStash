"""The supported Python runtime and numeric dependency floor agree across builds.

Retired interpreter pins must not survive in a CI lane or a native image while
project metadata promises a different supported runtime.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement

from tests.paths import REPO_ROOT

CURRENT_PYTHON = "3.14.8"
PROJECT_PATHS = (
    "backend/pyproject.toml",
    "backend/packages/printstash-core/pyproject.toml",
)
MESH_VERSIONS = {
    "numpy": "2.5.3",
    "scipy": "1.18.1",
    "trimesh": "5.1.1",
    "pillow": "12.3.0",
    "lxml": "6.1.3",
}


def _toml(path: str) -> dict:
    return tomllib.loads((REPO_ROOT / path).read_text())


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _python_steps() -> list[dict]:
    return [
        step
        for path in (REPO_ROOT / ".github/workflows").glob("*.yml")
        for job in _workflow(path)["jobs"].values()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("actions/setup-python@")
    ]


class TestPythonRuntime:
    @pytest.mark.parametrize("project_path", PROJECT_PATHS, ids=("backend", "core"))
    def test_declares_current_python_floor(self, project_path: str) -> None:
        project = _toml(project_path)

        assert project["project"]["requires-python"] == ">=3.14"
        assert not any(
            "python_version" in dependency
            for dependencies in project["project"]["optional-dependencies"].values()
            for dependency in dependencies
        )

    def test_pins_developer_runtime(self) -> None:
        mise = _toml(".mise.toml")

        assert mise["tools"]["python"] == CURRENT_PYTHON
        assert (REPO_ROOT / ".python-version").read_text().strip() == CURRENT_PYTHON

    def test_targets_current_python_in_static_tools(self) -> None:
        backend = _toml(PROJECT_PATHS[0])
        core = _toml(PROJECT_PATHS[1])
        backend_types = json.loads(
            (REPO_ROOT / "backend/pyrightconfig.json").read_text()
        )

        assert backend["tool"]["ruff"]["target-version"] == "py314"
        assert core["tool"]["ruff"]["target-version"] == "py314"
        assert backend_types["pythonVersion"] == "3.14"
        assert core["tool"]["pyright"]["pythonVersion"] == "3.14"

    @pytest.mark.parametrize("project_path", PROJECT_PATHS, ids=("backend", "core"))
    def test_excludes_retired_numpy_type_checker(self, project_path: str) -> None:
        requirements = (
            Requirement(value)
            for value in _toml(project_path)["project"]["optional-dependencies"]["dev"]
        )
        checker = next(value for value in requirements if value.name == "pyright")

        assert not checker.specifier.contains("1.1.403")
        assert checker.specifier.contains("1.1.414")

    def test_runs_ci_with_current_python(self) -> None:
        steps = _python_steps()
        deep = _workflow(REPO_ROOT / ".github/workflows/deep-ci.yml")
        normal_source = (REPO_ROOT / ".github/workflows/ci.yml").read_text()

        assert steps
        assert {step["with"]["python-version"] for step in steps} == {
            CURRENT_PYTHON,
            "${{ matrix.python }}",
        }
        assert deep["jobs"]["printer-core"]["strategy"]["matrix"]["python"] == [
            CURRENT_PYTHON
        ]
        assert f"uv venv --python {CURRENT_PYTHON}" in normal_source
        compatibility_steps = deep["jobs"]["backend-python314"]["steps"]
        suites = [
            (index, step)
            for index, step in enumerate(compatibility_steps)
            if step.get("name") == "Run backend compatibility suite"
        ]
        cleanups = [
            (index, step)
            for index, step in enumerate(compatibility_steps)
            if step.get("name") == "Remove PID namespace qualification"
        ]
        assert len(suites) == len(cleanups) == 1
        suite_index, suite = suites[0]
        cleanup_index, cleanup = cleanups[0]
        assert suite["run"] == "./scripts/test.sh ${{ matrix.phase }} -q"
        assert cleanup_index > suite_index
        assert cleanup["if"] == "always()"
        assert cleanup["run"] == "bash ../scripts/prepare-ci-pid-namespace.sh cleanup"

    def test_keeps_normal_check_topology(self) -> None:
        jobs = _workflow(REPO_ROOT / ".github/workflows/ci.yml")["jobs"]

        assert set(jobs) == {
            "backend",
            "printer-core",
            "frontend",
            "browser-smoke",
            "browser-extension",
            "gate",
        }
        assert len(jobs["backend"]["strategy"]["matrix"]["include"]) == 12
        assert (
            len(jobs) - 1 + len(jobs["backend"]["strategy"]["matrix"]["include"]) == 17
        )

    @pytest.mark.parametrize(
        "dockerfile",
        ("backend/Dockerfile", "deploy/manual-testing/emulators/Dockerfile"),
        ids=("backend", "emulator"),
    )
    def test_uses_current_python_images(self, dockerfile: str) -> None:
        source = (REPO_ROOT / dockerfile).read_text()
        python_images = re.findall(r"^FROM (python:[^\s]+)", source, flags=re.MULTILINE)

        assert python_images
        assert set(python_images) == {f"python:{CURRENT_PYTHON}-slim-trixie"}
        assert (REPO_ROOT / "backend/unified/Dockerfile").read_text().splitlines()[
            1
        ] == ("FROM api-image")

    @pytest.mark.parametrize("package_name", MESH_VERSIONS, ids=MESH_VERSIONS)
    def test_pins_current_mesh_dependencies(self, package_name: str) -> None:
        lock = _toml("backend/uv.lock")
        packages = [
            package for package in lock["package"] if package["name"] == package_name
        ]

        assert lock["requires-python"] == ">=3.14"
        assert [package["version"] for package in packages] == [
            MESH_VERSIONS[package_name]
        ]

    @pytest.mark.parametrize("scope", ["default", "full", "dev"], ids=str)
    def test_keeps_gpu_research_dependencies_optional(self, scope: str) -> None:
        metadata = _toml(PROJECT_PATHS[0])
        requirements = {
            "default": metadata["project"]["dependencies"],
            "full": metadata["project"]["optional-dependencies"]["full"],
            "dev": metadata["project"]["optional-dependencies"]["dev"],
        }
        research = {"moderngl", "glcontext"}

        assert not research.intersection(
            Requirement(value).name.lower() for value in requirements[scope]
        )
        assert {
            Requirement(value).name.lower()
            for value in metadata["project"]["optional-dependencies"]["gpu-pilot"]
        } == research
        assert (
            "gpu-pilot"
            in metadata["tool"]["deptry"]["optional_dependencies_dev_groups"]
        )
