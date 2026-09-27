#!/usr/bin/env python3
"""Check release tag against all shipped version declarations."""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def declared_versions() -> dict[str, str]:
    backend = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())
    frontend = json.loads((ROOT / "frontend/package.json").read_text())
    config = ast.parse((ROOT / "backend/app/core/config.py").read_text())
    app_versions = [
        node.value.value
        for node in ast.walk(config)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "app_version"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(app_versions) != 1:
        raise ValueError("expected one literal app_version in backend config")
    changelog = (ROOT / "frontend/src/lib/changelog.ts").read_text()
    marker = "export const CHANGELOG: ChangelogEntry[] = ["
    if marker not in changelog:
        raise ValueError("cannot locate in-app changelog")
    first_entry = re.search(r'\bversion:\s*"([^"]+)"', changelog.split(marker, 1)[1])
    if first_entry is None:
        raise ValueError("cannot locate first in-app changelog version")
    return {
        "backend project": backend["project"]["version"],
        "backend app": app_versions[0],
        "frontend": frontend["version"],
        "in-app changelog": first_entry.group(1),
    }


def main() -> int:
    if len(sys.argv) != 2 or re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", sys.argv[1]) is None:
        print("usage: verify-release-version.py vX.Y.Z", file=sys.stderr)
        return 2
    expected = sys.argv[1][1:]
    versions = declared_versions()
    mismatches = {name: value for name, value in versions.items() if value != expected}
    if mismatches:
        print(f"Release tag {sys.argv[1]} disagrees with: {mismatches}", file=sys.stderr)
        return 1
    print(f"Release declarations match {sys.argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
