"""Foreground arrivals cannot starve eligible backfill on real DBOS/native work."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import psutil
import pytest

from tests.e2e._processes import vault_environment
from tests.paths import BACKEND_DIR

# Each test vault has an independent admission pool. Full-host CPU/RAM
# measurements must not compete with unrelated xdist workers. Both assertions
# remain mandatory in the serial coverage and compatibility resource phases.
pytestmark = pytest.mark.native_host


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


@pytest.fixture(scope="module")
def fairness_report(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("ingestion-fairness")
    environment = vault_environment(root, f"sqlite:///{root / 'vault.sqlite'}")
    environment.update(
        VAULT_DATA_ROOT=str(root),
        VAULT_PROCESS_ROLE="all",
        VAULT_API_RUNS_JOBS="true",
        VAULT_MAX_RENDER_JOBS="2",
        VAULT_JOBS_DERIVE_NATIVE_CONCURRENCY="6",
        # The helper deliberately queues six foreground uploads before
        # releasing native admission. The default per-user staging limit is
        # four; configure this bounded fixture premise without changing the
        # native scheduler, concurrency or physical resource limits.
        VAULT_STAGING_MAX_ACTIVE_PER_USER="6",
        VAULT_STAGING_MAX_PENDING="6",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.fakes.ingestion_fairness_process"],
        cwd=BACKEND_DIR,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        output, errors = process.communicate(timeout=130)
        assert process.returncode == 0, (output + errors)[-12000:]
        outcomes = [
            json.loads(line) for line in output.splitlines() if line.startswith("{")
        ]
        assert outcomes, output + errors
        return outcomes[-1]
    finally:
        _stop(process)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


class TestIngestionFairness:
    def test_backfill_progresses_during_sustained_interactive_arrivals(
        self, fairness_report
    ):
        report = fairness_report
        assert report["arrival_window"] == 6, report
        assert report["initial_foreground_pending"] == report["arrival_window"], report
        assert report["max_foreground_pending"] == report["arrival_window"], report
        assert report["max_foreground_observed_jobs"] == report["arrival_window"], (
            report
        )
        assert report["staging_active_per_user_limit"] == report["arrival_window"], (
            report
        )
        assert report["staging_pending_limit"] == report["arrival_window"], report
        assert report["submitted"] > report["arrival_window"], report
        assert report["submitted"] <= report["arrival_budget"], report
        assert report["foreground_ready"] == report["submitted"], report
        assert report["foreground_progress_during_contention"] > 0, report
        assert report["backfill_ready"] == 2, report
        assert all(
            pending > 0
            for pending in report["foreground_pending_at_backfill_completion"]
        ), report
        assert report["max_active_backfill_jobs"] == 1, report
        assert report["contention_samples"] >= 10, report
        assert report["max_native_slots"] <= report["capacity_slots"], report
        assert report["max_native_bytes"] <= report["capacity_bytes"], report

    def test_full_capacity_backfill_eventually_completes(self, fairness_report):
        report = fairness_report
        assert report["queued_full_capacity_backfill"] is True, report
        assert report["admitted_full_capacity_backfill"] is True, report
        assert len(report["backfill_completion_seconds"]) == 2, report
        assert all(
            0 < elapsed < 110 for elapsed in report["backfill_completion_seconds"]
        ), report
        assert all(
            pending > 0
            for pending in report["foreground_pending_at_backfill_completion"]
        ), report
        assert report["backfill_ready"] == 2, report
