"""The standalone deployment works without a checkout or environment file.

Removing optional settings must preserve persistent state, private API access,
and startup ordering. Documentation fragments must compose into that same stack.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.paths import REPO_ROOT


@pytest.fixture
def compose_dir(tmp_path: Path) -> Path:
    (tmp_path / "docker-compose.yml").write_text(
        (REPO_ROOT / "docker-compose.simple.yml").read_text()
    )
    return tmp_path


def _render(directory: Path, **environment: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=directory,
        env={"PATH": os.environ["PATH"], **environment},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


@pytest.fixture
def simple_config(compose_dir: Path) -> dict[str, Any]:
    return _render(compose_dir)


class TestSimpleCompose:
    def test_runs_only_the_app_without_configuration(
        self, simple_config: dict[str, Any]
    ) -> None:
        assert set(simple_config["services"]) == {"frontend", "api"}
        assert not simple_config["services"]["api"].get("env_file")

    @pytest.mark.parametrize("service", ["api", "frontend"], ids=str)
    def test_uses_prebuilt_full_images(
        self, simple_config: dict[str, Any], service: str
    ) -> None:
        config = simple_config["services"][service]

        assert config["image"] == f"ghcr.io/xiao-villamor/printstash-{service}:latest"
        assert "build" not in config

    @pytest.mark.parametrize(
        ("volume", "target"),
        [
            pytest.param("printstash_data", "/data/files", id="uploads"),
            pytest.param("printstash_thumbs", "/data/thumbs", id="thumbnails"),
            pytest.param("printstash_db", "/data/db", id="database-credentials"),
            pytest.param("printstash_staging", "/data/staging", id="pending-imports"),
            pytest.param("printstash_backups", "/data/backups", id="backups"),
        ],
    )
    def test_persists_application_state(
        self, simple_config: dict[str, Any], volume: str, target: str
    ) -> None:
        mounts = simple_config["services"]["api"]["volumes"]

        assert {mount["target"]: mount["source"] for mount in mounts}[target] == volume
        assert volume in simple_config["volumes"]
        assert (
            next(mount for mount in mounts if mount["target"] == target)["type"]
            == "volume"
        )

    def test_exposes_only_the_web_port(self, simple_config: dict[str, Any]) -> None:
        services = simple_config["services"]

        assert not services["api"].get("ports")
        assert [
            (p["published"], p["target"]) for p in services["frontend"]["ports"]
        ] == [("3000", 3000)]

    def test_waits_for_api_health(self, simple_config: dict[str, Any]) -> None:
        services = simple_config["services"]

        assert (
            services["frontend"]["depends_on"]["api"]["condition"] == "service_healthy"
        )
        assert services["api"]["healthcheck"]["test"] == [
            "CMD",
            "curl",
            "-fsS",
            "http://localhost:8000/api/v1/health",
        ]
        assert not services["api"].get("entrypoint")
        assert not services["api"].get("command")

    def test_supervises_settings_restart(self, simple_config: dict[str, Any]) -> None:
        api = simple_config["services"]["api"]

        assert api["environment"]["VAULT_RESTART_ENABLED"] == "true"
        assert api["restart"] == "unless-stopped"

    def test_accepts_optional_image_tag(self, compose_dir: Path) -> None:
        config = _render(compose_dir, PRINTSTASH_VERSION="test-release")

        assert {s["image"].rsplit(":", 1)[1] for s in config["services"].values()} == {
            "test-release"
        }

    def test_accepts_optional_web_port(self, compose_dir: Path) -> None:
        config = _render(compose_dir, PRINTSTASH_HTTP_PORT="8080")

        assert config["services"]["frontend"]["ports"][0]["published"] == "8080"

    def test_ignores_unwired_host_database_setting(self, compose_dir: Path) -> None:
        config = _render(compose_dir, VAULT_DB_URL="sqlite:///./ephemeral.sqlite")

        assert "VAULT_DB_URL" not in config["services"]["api"]["environment"]

    @pytest.mark.parametrize(
        "fragment",
        re.findall(
            r"```yaml\n(services:\n.*?)```",
            (REPO_ROOT / "docs/deployment.md").read_text(),
            re.DOTALL,
        ),
        ids=["session-lifetime", "setup-token", "host-folders", "upload-limit"],
    )
    def test_documented_overrides_preserve_startup(
        self, compose_dir: Path, fragment: str
    ) -> None:
        (compose_dir / "docker-compose.override.yml").write_text(fragment)

        config = _render(compose_dir, VAULT_SETUP_TOKEN="test-setup-token")

        assert set(config["services"]) == {"frontend", "api"}
        assert (
            config["services"]["api"]["environment"]["VAULT_RESTART_ENABLED"] == "true"
        )
        assert (
            config["services"]["frontend"]["depends_on"]["api"]["condition"]
            == "service_healthy"
        )
