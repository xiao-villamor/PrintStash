"""A dead preview worker is reconciled on restart without losing the cover."""

import json
import os
import signal
import subprocess
import sys
import tempfile

from tests.e2e._processes import _first_outcome, _stop, vault_environment
from tests.paths import BACKEND_DIR


class TestPreviewRecovery:
    def test_survives_worker_restart(self, tmp_path):
        environment = vault_environment(
            tmp_path, f"sqlite:///{tmp_path / 'vault.sqlite'}"
        )
        environment.update(
            VAULT_DATA_ROOT=str(tmp_path),
            VAULT_PROCESS_ROLE="all",
            VAULT_API_RUNS_JOBS="true",
        )
        native_pid = None
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "tests.fakes.preview_recovery_process", "hold"],
                cwd=BACKEND_DIR,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                start_new_session=True,
            )
            try:
                held = _first_outcome(process, log)
                native_pid = held["native_pid"]
                assert held["preview_states"] == ["running"]
                assert held["published_count"] == 0
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=15)
                os.killpg(native_pid, signal.SIGKILL)
                # Re-open the same vault using the real DBOS/reconciler composition.
                restarted = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "tests.fakes.preview_recovery_process",
                        "converge",
                        str(held["file_id"]),
                    ],
                    cwd=BACKEND_DIR,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                assert restarted.returncode == 0, restarted.stdout + restarted.stderr
                rows = [
                    json.loads(line)
                    for line in restarted.stdout.splitlines()
                    if line.startswith("{")
                ]
                outcome = rows[-1]
                assert outcome["preview_states"] == ["ready"], outcome
                assert outcome["published_count"] == 1
                assert outcome["primary_sha256"] == held["primary_sha256"]
            finally:
                _stop(process, native_pid)
