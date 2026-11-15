"""The image ships the DBOS notification patch on, matched to the locked SDK.

A DBOS upgrade that forgets to regenerate the patch would fail the image build
(the patch refuses unknown sources); these checks fail first, in the PR.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import tomllib
from pathlib import Path

import yaml

from app.runtime.engine import dbos_notifications
from tests.paths import BACKEND_DIR, REPO_ROOT

DOCKERFILE = BACKEND_DIR / "Dockerfile"
PATCH_SCRIPT = BACKEND_DIR / "scripts" / "experimental_dbos_notifications.py"


def _locked_dbos_version() -> str:
    lock = tomllib.loads((BACKEND_DIR / "uv.lock").read_text())
    (package,) = [p for p in lock["package"] if p["name"] == "dbos"]
    return package["version"]


def _patch_constants() -> tuple[dict[str, str], str]:
    source = PATCH_SCRIPT.read_text()
    expected = dict(re.findall(r'"(_[a-z_]+\.py)": "([0-9a-f]{64})"', source))
    (version,) = re.findall(r'"dbos_version": "([0-9.]+)"', source)
    return expected, version


def test_the_runtime_stage_builds_the_patch_and_enables_it_from_one_argument():
    runtime = DOCKERFILE.read_text().split(" AS runtime\n", 1)[1]

    assert "ARG PRINTSTASH_DBOS_NOTIFICATIONS=true\n" in runtime
    patch_at = runtime.index("scripts/experimental_dbos_notifications.py \\")
    enable_at = runtime.index(
        "ENV VAULT_JOBS_SQLITE_QUEUE_NOTIFICATIONS=${PRINTSTASH_DBOS_NOTIFICATIONS}\n"
    )
    assert patch_at < enable_at


def test_advanced_compose_keeps_the_image_default():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.advanced.yml").read_text())
    values = [
        service.get("environment", {}).get("VAULT_JOBS_SQLITE_QUEUE_NOTIFICATIONS")
        for service in compose["services"].values()
        if isinstance(service.get("environment"), dict)
    ]

    assert "${VAULT_JOBS_SQLITE_QUEUE_NOTIFICATIONS:-true}" in values
    assert "${VAULT_JOBS_SQLITE_QUEUE_NOTIFICATIONS:-false}" not in values


def test_the_patch_targets_the_locked_and_installed_sdk():
    expected, version = _patch_constants()
    spec = importlib.util.find_spec("dbos")
    assert spec is not None and spec.submodule_search_locations
    installed = Path(next(iter(spec.submodule_search_locations)))

    assert set(expected) == dbos_notifications.PATCH_FILES
    assert version == _locked_dbos_version()
    assert {
        name: hashlib.sha256((installed / name).read_bytes()).hexdigest()
        for name in expected
    } == expected
