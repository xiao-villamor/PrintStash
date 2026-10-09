"""A real private compute broker recovers after process death without GPU dependencies."""

import json
import os
import subprocess
import sys

from tests.paths import REPO_ROOT


class TestComputeBroker:
    def test_restarts_after_owner_death(self, tmp_path):
        env = {**os.environ, "VAULT_COMPUTE_MODE": "cpu"}
        result = subprocess.run(
            [sys.executable, "-m", "tests.fakes.compute_broker_probe", str(tmp_path)],
            cwd=REPO_ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

        assert result.returncode == 0, result.stderr
        evidence = json.loads(result.stdout)
        assert evidence["restarted"] is True
        assert evidence["second"]["mode"] == "cpu"
        assert evidence["socket_mode"] == 0o600
