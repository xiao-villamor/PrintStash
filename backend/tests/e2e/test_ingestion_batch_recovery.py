"""Accepted batches keep their first committed Artifact across real interruption.

Fresh application processes use real SQLite, storage, DBOS and public archive
routes. The process observer exposes a committed boundary; cancellation and
SIGKILL originate outside the worker. Expanded staging is observed throughout
an input larger than its one-member window, rather than only at first publish.
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
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import pytest

from tests.e2e._processes import vault_environment
from tests.factories.content import gcode, oversized_gcode, zip_bytes
from tests.paths import BACKEND_DIR

_ROLE = "tests.fakes.ingestion_batch_process"
_WINDOW_BYTES = 1024 * 1024


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=10)


def _ready(process: subprocess.Popen, log) -> dict:
    assert process.stdout is not None
    deadline = time.monotonic() + 85
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
    raise AssertionError(
        f"batch process never exposed its commit:\n{log.read()[-6000:]}"
    )


def _outcome(process: subprocess.Popen, log, command: str | None = None) -> dict:
    output, _ = process.communicate(input=command, timeout=100)
    log.flush()
    log.seek(0)
    assert process.returncode == 0, output + log.read()[-6000:]
    outcomes = [
        json.loads(line) for line in output.splitlines() if line.startswith("{")
    ]
    assert outcomes, output
    return outcomes[-1]


@dataclass
class BatchProcess:
    environment: dict[str, str]
    archive: Path
    payloads: tuple[bytes, ...]
    processes: list[subprocess.Popen]
    log: TextIO

    def start(self, role: str, argument: str) -> subprocess.Popen:
        environment = dict(self.environment)
        if role == "start":
            # This child is deliberately killed in one case, so a coverage
            # shard cannot be expected to flush. Recovery exits normally.
            environment.pop("COVERAGE_PROCESS_CONFIG", None)
        process = subprocess.Popen(
            [sys.executable, "-m", _ROLE, role, argument],
            cwd=BACKEND_DIR,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            start_new_session=True,
        )
        self.processes.append(process)
        return process


@pytest.fixture
def batch_process(tmp_path: Path):
    environment = vault_environment(tmp_path, f"sqlite:///{tmp_path / 'vault.sqlite'}")
    environment.update(
        VAULT_PROCESS_ROLE="all",
        VAULT_API_RUNS_JOBS="true",
        VAULT_INGESTION_BATCH_MAX_FILES="1",
        VAULT_INGESTION_BATCH_MAX_MB="1",
    )
    payloads = tuple(
        oversized_gcode(600_000) + gcode(marker=f"real-batch-member-{index}")
        for index in range(4)
    )
    archive = tmp_path / "batch.zip"
    archive.write_bytes(
        zip_bytes(
            {f"part-{index}.gcode": payload for index, payload in enumerate(payloads)},
            compress=False,
        )
    )
    processes = []
    with tempfile.TemporaryFile(mode="w+") as log:
        batch = BatchProcess(environment, archive, payloads, processes, log)
        try:
            yield batch
        finally:
            for process in processes:
                _kill(process)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stdin is not None:
                    process.stdin.close()


class TestBatchInterruption:
    @pytest.mark.critical
    def test_cancels_before_consuming_the_next_entry(self, batch_process) -> None:
        process = batch_process.start("start", str(batch_process.archive))
        held = _ready(process, batch_process.log)
        assert (
            held["first"]["sha256"]
            == hashlib.sha256(batch_process.payloads[0]).hexdigest()
        )

        outcome = _outcome(process, batch_process.log, "cancel\n")

        assert outcome["job"]["state"] == "cancelled", outcome
        assert outcome["originals"] == [held["first"]], outcome
        assert len(outcome["entries"]) == len(batch_process.payloads), outcome
        assert [entry["state"] for entry in outcome["entries"]] == [
            "imported",
            "pending",
            "pending",
            "pending",
        ], outcome
        assert all(entry["file_id"] is None for entry in outcome["entries"][1:]), (
            outcome
        )
        assert len(outcome["expanded_paths"]) == 1, outcome
        assert outcome["entries"][0]["file_id"] == held["first"]["file_id"]
        assert outcome["entries"][0]["state"] == "imported"

    @pytest.mark.critical
    def test_recovers_a_batch_after_process_death(self, batch_process) -> None:
        interrupted = batch_process.start("start", str(batch_process.archive))
        held = _ready(interrupted, batch_process.log)
        assert (
            held["first"]["sha256"]
            == hashlib.sha256(batch_process.payloads[0]).hexdigest()
        )

        _kill(interrupted)
        assert interrupted.returncode == -signal.SIGKILL
        restarted = batch_process.start("recover", held["job_id"])
        outcome = _outcome(restarted, batch_process.log)

        assert outcome["job"]["state"] == "completed", outcome
        assert outcome["total_files"] == len(batch_process.payloads), outcome
        assert len(outcome["entries"]) == len(batch_process.payloads), outcome
        assert outcome["originals"][0] == held["first"], outcome
        assert {item["sha256"] for item in outcome["originals"]} == {
            hashlib.sha256(payload).hexdigest() for payload in batch_process.payloads
        }, outcome
        assert len({entry["file_id"] for entry in outcome["entries"]}) == len(
            batch_process.payloads
        ), outcome
        assert outcome["attempts"] > 1 or outcome["resubmits"] > 0, outcome

    @pytest.mark.critical
    def test_bounds_peak_staging_throughout_the_batch(self, batch_process) -> None:
        assert sum(map(len, batch_process.payloads)) > 2 * _WINDOW_BYTES
        process = batch_process.start("start", str(batch_process.archive))
        held = _ready(process, batch_process.log)

        outcome = _outcome(process, batch_process.log, "continue\n")

        assert outcome["job"]["state"] == "completed", outcome
        assert outcome["sample_count"] > len(batch_process.payloads), outcome
        assert len(outcome["expanded_paths"]) == len(batch_process.payloads), outcome
        assert set(range(1, len(batch_process.payloads) + 1)) <= set(
            outcome["commit_counts"]
        ), outcome
        # A directory scan may see one inode before and after quarantine
        # rename, or at hardlink aliases. Charge its largest observed extent
        # once, while retaining logical totals and all paths as diagnostics.
        for total, files, extent in (
            ("peak_staging_bytes", "peak_staging_files", "size"),
            (
                "peak_allocated_staging_bytes",
                "peak_allocated_staging_files",
                "allocated_bytes",
            ),
        ):
            by_inode = {}
            for item in outcome[files]:
                identity = (item["device"], item["inode"])
                by_inode[identity] = max(by_inode.get(identity, 0), item[extent])
            assert outcome[total] == sum(by_inode.values()), outcome
        assert outcome["peak_logical_staging_bytes"] == sum(
            item["size"] for item in outcome["peak_logical_staging_files"]
        ), outcome
        assert outcome["peak_logical_staging_bytes"] >= outcome["peak_staging_bytes"], (
            outcome
        )
        assert outcome["peak_staging_bytes"] >= held["staging_baseline"] + max(
            map(len, batch_process.payloads)
        ), outcome
        assert outcome["peak_staging_bytes"] <= (
            held["staging_baseline"] + _WINDOW_BYTES
        ), outcome
        assert {item["sha256"] for item in outcome["originals"]} == {
            hashlib.sha256(payload).hexdigest() for payload in batch_process.payloads
        }, outcome
