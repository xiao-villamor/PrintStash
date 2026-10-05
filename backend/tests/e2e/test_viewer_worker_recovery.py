"""A real viewer-STL native worker can die without losing its Artifact.

Demand, refusal and retry travel through public routes on the real application
and DBOS engine. The only fault is an OS signal to the observed native worker.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
import pytest

from tests.e2e._processes import vault_environment
from tests.paths import BACKEND_DIR


def _first_outcome(process: subprocess.Popen, log) -> dict:
    assert process.stdout is not None
    deadline = time.monotonic() + 80
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(timeout=0.2):
                continue
            line = process.stdout.readline()
            if line.startswith("{"):
                return json.loads(line)
            if not line and process.poll() is not None:
                break
    log.flush()
    log.seek(0)
    raise AssertionError(f"viewer worker did not become ready:\n{log.read()[-6000:]}")


def _stop(process: subprocess.Popen, native_pid: int | None) -> None:
    if process.poll() is None:
        try:
            descendants = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            descendants = []
        for descendant in descendants:
            try:
                descendant.kill()
            except psutil.NoSuchProcess:
                pass
    for pid in (native_pid, process.pid):
        if pid is None:
            continue
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)
    if process.stdout is not None:
        process.stdout.close()


class TestViewerWorkerRecovery:
    @pytest.mark.critical
    def test_viewer_retry_recovers_after_native_worker_death(
        self, tmp_path: Path
    ) -> None:
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
                [sys.executable, "-m", "tests.fakes.viewer_recovery_process", "viewer"],
                cwd=BACKEND_DIR,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                start_new_session=True,
            )
            try:
                held = _first_outcome(process, log)
                assert held["stage"] == "held", held
                native_pid = held["native_pid"]
                worker = psutil.Process(native_pid)
                assert worker.status() == psutil.STATUS_STOPPED
                assert "app.modules.media.stl_worker" in worker.cmdline()
                for name in ("native_ticket", "preparation_ticket", "native_temporary"):
                    assert Path(held[name]).is_relative_to(tmp_path), held
                    assert Path(held[name]).exists()
                assert Path(held["prepared_workspace"]).is_relative_to(tmp_path)
                assert Path(held["prepared_workspace"]).exists()
                os.kill(native_pid, signal.SIGKILL)
                outcome_text, _ = process.communicate(timeout=90)
                log.flush()
                log.seek(0)
                assert process.returncode == 0, outcome_text + log.read()[-6000:]
                outcomes = [
                    json.loads(line)
                    for line in outcome_text.splitlines()
                    if line.startswith("{")
                ]
                assert outcomes
                recovered = outcomes[-1]
                assert recovered["stage"] == "recovered"
                assert recovered["failure_reason"] == "resource_limit"
                assert recovered["triangle_count"] == 4
                assert recovered["watertight"] is True
                assert recovered["original_preserved"] is True
                assert recovered["viewer_job_count"] == 2
                assert recovered["failed_job_settled"] is True
                assert recovered["failed_credits_released"] is True
                assert len(recovered["publication_sha256"]) == 64
                assert not psutil.pid_exists(native_pid)
                assert not Path(held["native_ticket"]).exists()
                assert not Path(held["preparation_ticket"]).exists()
                assert not Path(held["native_temporary"]).exists()
                assert not Path(held["prepared_workspace"]).exists()
            finally:
                _stop(process, native_pid)
