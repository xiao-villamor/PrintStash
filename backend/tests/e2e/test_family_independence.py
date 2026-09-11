"""A real installation can expose manual Families without related feature code."""

import os
import shutil
import subprocess
import sys

from tests.paths import BACKEND_DIR


class TestFamilyIndependence:
    def test_manual_family_flow_without_related_feature_packages(self, tmp_path):
        isolated = tmp_path / "installation"
        excluded = {"__pycache__", "similarity", "inference"}
        shutil.copytree(
            BACKEND_DIR / "app",
            isolated / "app",
            ignore=lambda _directory, names: set(names) & excluded,
        )
        environment = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(isolated), str(BACKEND_DIR))),
            "VAULT_DB_URL": f"sqlite:///{tmp_path / 'vault.sqlite'}",
            "VAULT_SETUP_MODE": "trusted_network",
            "VAULT_SETUP_ALLOWED_HOSTS": "testserver",
            "VAULT_SECRETS_KEY": "family-independence-test-key",
            "VAULT_SECRETS_KEY_FILE": str(tmp_path / "secrets-key"),
        }
        for key in (
            "DATA_DIR",
            "THUMB_DIR",
            "STAGING_DIR",
            "BACKUP_DIR",
            "ARTIFACT_CACHE_ROOT",
        ):
            directory = tmp_path / key.lower()
            directory.mkdir()
            environment[f"VAULT_{key}"] = str(directory)
        result = subprocess.run(
            [sys.executable, "-m", "tests.fakes.family_standalone"],
            cwd=isolated,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "family-independent-flow-complete" in result.stdout
