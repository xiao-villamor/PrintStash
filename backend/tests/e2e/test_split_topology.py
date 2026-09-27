"""An API that runs no jobs, and two workers that run them, on one vault.

The third supported topology (``docs/architecture/background-work.md``):
PostgreSQL, one volume every process mounts, an API with
``VAULT_API_RUNS_JOBS=false`` and ``python -m app.worker`` replicas. Every
process here is a real child on real DBOS:

- work accepted while no worker exists waits, durably, and the workers that
  come up afterwards run it to convergence;
- the API's own client hears each change of its Job, although a worker made
  it, because workers publish over NOTIFY and the API delivers locally;
- the reconciler tick every worker schedules runs once per occurrence across
  the deployment, not once per process;
- a worker stops cleanly on SIGTERM and takes itself off the executor list.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.db.migrate import run_migrations
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.e2e._processes import vault_environment
from tests.paths import BACKEND_DIR

pytestmark = pytest.mark.postgres

_ROLE = "tests.fakes.job_engine_process"
_TIMEOUT_S = 180


@pytest.fixture
def split_vault(tmp_path: Path) -> dict[str, str]:
    db_url = fresh_postgres_database("split")
    # The container entrypoint migrates before the API starts; workers never
    # migrate and wait for exactly this schema.
    run_migrations(db_url)
    environment = vault_environment(tmp_path, db_url)
    environment["VAULT_SHARED_STORAGE"] = "true"
    return environment


@pytest.fixture
def start_worker(split_vault: dict[str, str]) -> Iterator:
    started: list[subprocess.Popen] = []

    def start() -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.worker"],
            cwd=BACKEND_DIR,
            env={**split_vault, "VAULT_PROCESS_ROLE": "worker"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        started.append(process)
        return process

    yield start
    for process in started:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)


def _start_api(environment: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", _ROLE, "split_api"],
        cwd=BACKEND_DIR,
        env={
            **environment,
            "VAULT_PROCESS_ROLE": "api",
            "VAULT_API_RUNS_JOBS": "false",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _line(process: subprocess.Popen) -> dict:
    assert process.stdout is not None
    for line in process.stdout:
        if line.startswith("{"):
            return json.loads(line)
    _, stderr = process.communicate(timeout=30)
    raise AssertionError(f"API process exited early:\n{stderr[-6000:]}")


def _workers(environment: dict[str, str]) -> list[str]:
    engine = create_engine(normalize_database_url(environment["VAULT_DB_URL"]))
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text("SELECT role FROM work_executors ORDER BY role")
                ).scalars()
            )
    finally:
        engine.dispose()


def _tick_occurrences(environment: dict[str, str]) -> list[int]:
    """The reconcile interval each recorded tick fell in."""
    interval_ms = int(environment["VAULT_JOBS_RECONCILE_INTERVAL_SECONDS"]) * 1000
    engine = create_engine(normalize_database_url(environment["VAULT_DB_URL"]))
    try:
        with engine.connect() as connection:
            created = connection.execute(
                text(
                    "SELECT created_at FROM dbos.workflow_status "
                    "WHERE name = 'printstash.tick'"
                )
            ).scalars()
            return [int(value) // interval_ms for value in created]
    finally:
        engine.dispose()


@pytest.fixture
def converged(split_vault: dict[str, str], start_worker) -> dict:
    api = _start_api(split_vault)
    try:
        accepted = _line(api)
        workers = [start_worker(), start_worker()]
        outcome = _line(api)
        api.wait(timeout=_TIMEOUT_S)
    finally:
        if api.poll() is None:
            api.kill()
            api.wait(timeout=30)
    return {**outcome, **accepted, "workers": workers, "env": split_vault}


class TestSplitTopology:
    @pytest.mark.critical
    def test_workers_run_what_the_api_accepted(self, converged: dict) -> None:
        assert converged["states"] == {"metadata": "ready", "thumbnail": "ready"}

    def test_the_apis_client_hears_what_a_worker_did(self, converged: dict) -> None:
        states = [notice["state"] for notice in converged["notices"]]

        assert states[-1] == "completed"
        assert all(notice["type"] == "job" for notice in converged["notices"])

    def test_each_tick_runs_once_across_every_process(self, converged: dict) -> None:
        # Every process that runs jobs schedules the reconciler tick. Two ticks
        # inside one interval would mean an occurrence ran once per process.
        deadline = time.monotonic() + 90
        while len(occurrences := _tick_occurrences(converged["env"])) < 3:
            assert time.monotonic() < deadline, f"ticks recorded: {occurrences}"
            time.sleep(1)

        assert len(occurrences) == len(set(occurrences)), occurrences

    def test_a_worker_leaves_cleanly_on_sigterm(self, converged: dict) -> None:
        assert _workers(converged["env"]).count("worker") == 2
        for worker in converged["workers"]:
            worker.send_signal(signal.SIGTERM)

        codes = [worker.wait(timeout=60) for worker in converged["workers"]]

        assert codes == [0, 0], [w.stderr.read()[-4000:] for w in converged["workers"]]
        deadline = time.monotonic() + 10
        while "worker" in _workers(converged["env"]):
            assert time.monotonic() < deadline, "a stopped worker stayed registered"
            time.sleep(0.2)
