"""Critical capabilities remain executable release blockers.

The manifest is the durable inventory: removing, renaming, or unmarking one of
its tests must fail even when pytest would otherwise silently collect fewer
critical cases.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from tests.paths import REPO_ROOT

MANIFEST = REPO_ROOT / "critical-capabilities.json"
REQUIRED_CAPABILITY_IDS = {
    "backup-browser-recovery",
    "backup-local-api",
    "backup-remote-only-api",
    "backup-remote-only-browser",
    "backup-restore-interruption",
    "backup-s3-only-recovery",
    "critical-lane-governance",
    "library-scan-local",
    "library-scan-remote",
    "model-gcode-ingestion",
    "model-lifecycle-browser",
    "model-mesh-ingestion",
    "model-organization",
    "remote-storage-lifecycle",
    "trash-purge",
}


def _decorator_name(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _pytest_tests(path: Path) -> dict[str, set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for group in tree.body:
        if not isinstance(group, ast.ClassDef) or not group.name.startswith("Test"):
            continue
        for node in group.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            found[f"{group.name}::{node.name}"] = {
                _decorator_name(decorator) for decorator in node.decorator_list
            }
    return found


class TestCriticalCapabilityManifest:
    @pytest.mark.critical
    def test_every_required_capability_names_a_marked_test(self) -> None:
        document = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert document["schema_version"] == 1
        assert document["lane"] == "scripts/test-critical.sh"
        lane = REPO_ROOT / document["lane"]
        assert lane.is_file()
        assert os.access(lane, os.X_OK)
        assert (
            "tests/repo/test_critical_capabilities.py::"
            "TestCriticalCapabilityManifest::"
            "test_every_required_capability_names_a_marked_test"
        ) in lane.read_text(encoding="utf-8")
        capabilities = document["capabilities"]
        assert capabilities
        assert len({row["id"] for row in capabilities}) == len(capabilities)
        assert {row["id"] for row in capabilities} == REQUIRED_CAPABILITY_IDS
        frontend_package = json.loads(
            (REPO_ROOT / "frontend/package.json").read_text(encoding="utf-8")
        )
        critical_browser_command = frontend_package["scripts"]["test:e2e:critical"]

        errors: list[str] = []
        for capability in capabilities:
            if capability.get("required") is not True:
                errors.append(f"{capability['id']}: capability is not required")
            tests = capability.get("tests", [])
            if not tests:
                errors.append(f"{capability['id']}: no tests declared")
            for target in tests:
                path = REPO_ROOT / target["path"]
                if not path.is_file():
                    errors.append(f"{capability['id']}: missing {target['path']}")
                    continue
                if target["runtime"] == "pytest":
                    collected = _pytest_tests(path)
                    node = target["node"]
                    if node not in collected:
                        errors.append(f"{capability['id']}: missing pytest node {node}")
                    elif "pytest.mark.critical" not in collected[node]:
                        errors.append(
                            f"{capability['id']}: {node} lost its critical marker"
                        )
                elif target["runtime"] == "playwright":
                    title = target["title"]
                    config = target["config"]
                    if not (REPO_ROOT / "frontend" / config).is_file():
                        errors.append(f"{capability['id']}: missing {config}")
                    if f"--config={config}" not in critical_browser_command:
                        errors.append(
                            f"{capability['id']}: critical lane does not run {config}"
                        )
                    if not title.startswith("@critical "):
                        errors.append(
                            f"{capability['id']}: title is not @critical: {title}"
                        )
                    if title not in path.read_text(encoding="utf-8"):
                        errors.append(
                            f"{capability['id']}: missing Playwright test {title}"
                        )
                else:
                    errors.append(
                        f"{capability['id']}: unknown runtime {target['runtime']}"
                    )

        assert not errors, "\n" + "\n".join(errors)
