"""A restarted application retires unfinished scratch bytes on real DBOS.

The first cleanup Job encounters a live writer and must retain its custody.
SIGKILL then prevents every Python finally/shutdown hook from running; only a
new application process and its durable Work Source can retire the payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.e2e._processes import vault_environment
from tests.paths import BACKEND_DIR

_ROLE = "tests.fakes.ingestion_scratch_process"


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def _ready(process: subprocess.Popen, log) -> dict:
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
    raise AssertionError(f"scratch writer did not become ready:\n{log.read()[-6000:]}")


class TestScratchCrashRecovery:
    @pytest.mark.critical
    def test_restarted_cleanup_job_retires_interrupted_payload(
        self, tmp_path: Path
    ) -> None:
        environment = vault_environment(
            tmp_path, f"sqlite:///{tmp_path / 'vault.sqlite'}"
        )
        environment.update(VAULT_PROCESS_ROLE="all", VAULT_API_RUNS_JOBS="true")
        killed_environment = dict(environment)
        # SIGKILL cannot flush an instrumented SQLite coverage shard. The
        # converging child still records normal coverage on its clean exit.
        killed_environment.pop("COVERAGE_PROCESS_CONFIG", None)
        processes = []
        with tempfile.TemporaryFile(mode="w+") as log:
            try:
                writer = subprocess.Popen(
                    [sys.executable, "-m", _ROLE, "stall"],
                    cwd=BACKEND_DIR,
                    env=killed_environment,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    start_new_session=True,
                )
                processes.append(writer)
                held = _ready(writer, log)
                partial = Path(held["partial"])
                assert partial.stat().st_size == held["bytes"]
                assert (
                    hashlib.sha256(partial.read_bytes()).hexdigest() == held["sha256"]
                )
                engine = create_engine(environment["VAULT_DB_URL"])
                try:
                    with engine.connect() as connection:
                        assert connection.execute(
                            text(
                                "SELECT output_name FROM ingestion_scratch_windows WHERE id = :id"
                            ),
                            {"id": held["window_id"]},
                        ).one() == (None,)
                        assert (
                            connection.execute(
                                text(
                                    "SELECT operation_id FROM capacity_reservations WHERE operation_id = :id"
                                ),
                                {"id": held["operation_id"]},
                            ).scalar_one()
                            == held["operation_id"]
                        )
                finally:
                    engine.dispose()
                _kill_group(writer)
                assert writer.returncode == -signal.SIGKILL
                assert partial.exists()
                restarted = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        _ROLE,
                        "converge",
                        held["window_id"],
                        held["operation_id"],
                        held["live_cleanup_job_id"],
                    ],
                    cwd=BACKEND_DIR,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    start_new_session=True,
                )
                processes.append(restarted)
                outcome_text, _ = restarted.communicate(timeout=90)
                log.flush()
                log.seek(0)
                assert restarted.returncode == 0, outcome_text + log.read()[-6000:]
                outcomes = [
                    json.loads(line)
                    for line in outcome_text.splitlines()
                    if line.startswith("{")
                ]
                assert outcomes
                outcome = outcomes[-1]
                assert outcome["receipt_present"] is False
                assert outcome["reservation_present"] is False
                assert any(
                    identifier != held["live_cleanup_job_id"]
                    for identifier in outcome["completed_cleanup_job_ids"]
                )
                assert not partial.exists()
                assert not Path(held["directory"]).exists()
                assert (
                    not Path(held["directory"])
                    .with_name(held["window_id"] + ".retired")
                    .exists()
                )
                assert not Path(held["lock_path"]).exists()
            finally:
                for process in processes:
                    _kill_group(process)
                    if process.stdout is not None:
                        process.stdout.close()
