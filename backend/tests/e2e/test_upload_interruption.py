"""An upload killed before its lease commits leaves no permanent staging leak.

The child copies a real upload into ``staging/_incoming`` and is SIGKILLed
before the lease is recorded, so no Python cleanup runs. A new application
process removes that orphan at owner startup once it is a day old, and keeps a
recent one that could still belong to a request in flight.
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
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine, text

from app.core.time import utcnow
from tests.e2e._processes import vault_environment
from tests.paths import BACKEND_DIR

_ROLE = "tests.fakes.upload_interruption_process"


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
    raise AssertionError(f"upload did not reach staging:\n{log.read()[-6000:]}")


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


class TestUploadKilledBeforeItsLease:
    def test_a_restarted_owner_sweeps_the_orphan_once_it_is_old(
        self, tmp_path: Path
    ) -> None:
        environment = vault_environment(
            tmp_path, f"sqlite:///{tmp_path / 'vault.sqlite'}"
        )
        environment.update(VAULT_PROCESS_ROLE="all", VAULT_API_RUNS_JOBS="true")
        killed_environment = dict(environment)
        # SIGKILL cannot flush an instrumented coverage shard.
        killed_environment.pop("COVERAGE_PROCESS_CONFIG", None)
        processes: list[subprocess.Popen] = []
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
                orphan = Path(held["partial"])
                assert orphan.stat().st_size == held["bytes"] > 0
                engine = create_engine(environment["VAULT_DB_URL"])
                try:
                    with engine.connect() as connection:
                        assert (
                            connection.execute(
                                text(
                                    "SELECT count(*) FROM staging_leases WHERE path = :p"
                                ),
                                {"p": str(orphan)},
                            ).scalar_one()
                            == 0
                        )
                finally:
                    engine.dispose()

                _kill_group(writer)

                assert writer.returncode == -signal.SIGKILL
                assert orphan.exists()
                stamp = (utcnow() - timedelta(days=2)).timestamp()
                os.utime(orphan, (stamp, stamp))
                recent = orphan.with_name("in-flight-upload.stl")
                recent.write_bytes(b"a request may still own this")

                restarted = subprocess.Popen(
                    [sys.executable, "-m", _ROLE, "startup", str(orphan), str(recent)],
                    cwd=BACKEND_DIR,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    start_new_session=True,
                )
                processes.append(restarted)
                output, _ = restarted.communicate(timeout=90)
                log.flush()
                log.seek(0)
                assert restarted.returncode == 0, output + log.read()[-6000:]
                (outcome,) = [
                    json.loads(line)
                    for line in output.splitlines()
                    if line.startswith("{")
                ]

                assert outcome["present"] == {str(orphan): False, str(recent): True}
            finally:
                for process in processes:
                    if process.poll() is None:
                        _kill_group(process)
