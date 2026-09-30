"""Persisted policy survives restart and controls already-running workers."""

import json
import subprocess
import sys

import pytest
from sqlmodel import create_engine, update

from app.db.models import SystemConfig
from app.db.url import normalize_database_url
from tests.containers import fresh_postgres_database
from tests.e2e._processes import vault_environment
from tests.paths import BACKEND_DIR


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def vault_env(request, tmp_path):
    url = (
        fresh_postgres_database("derivative_process")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'vault.sqlite'}"
    )
    return vault_environment(tmp_path, url)


ROLE = "tests.fakes.derivative_policy_process"


def invoke(environment, *arguments):
    result = subprocess.run(
        [sys.executable, "-m", ROLE, *arguments],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]
    return lines[-1]


def read_line(process):
    assert process.stdout is not None
    for line in process.stdout:
        if line.startswith("{"):
            return json.loads(line)
    stderr = process.stderr.read() if process.stderr else ""
    pytest.fail(f"policy process exited without an outcome: {stderr[-6000:]}")


class TestDurablePolicy:
    def test_saved_policy_survives_a_fresh_process(self, vault_env):
        invoke(vault_env, "disabled")
        assert invoke(
            {**vault_env, "VAULT_DERIVATIVES_MESH_ENABLED": "true"}, "read"
        ) == {"enabled": False}

    def test_disabled_crash_recovery_never_reexecutes_the_producer(
        self, vault_env, tmp_path
    ):
        marker = tmp_path / "producer-running"
        process = subprocess.Popen(
            [sys.executable, "-m", ROLE, "stall_storage", str(marker)],
            cwd=BACKEND_DIR,
            env=vault_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            started = read_line(process)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=30)
        engine = create_engine(normalize_database_url(vault_env["VAULT_DB_URL"]))
        try:
            with engine.begin() as connection:
                changed = connection.execute(
                    update(SystemConfig)
                    .where(SystemConfig.id == 1)
                    .values(derivatives_mesh_enabled=False)
                )
                assert changed.rowcount == 1
        finally:
            engine.dispose()
        outcome = invoke(vault_env, "recover_disabled", str(started["file_id"]))
        assert set(outcome["states"].values()) == {"failed"}
        assert set(outcome["attempts"].values()) == {1}
        assert set(outcome["errors"]) == {"derivative_group_disabled"}

    @pytest.mark.postgres
    def test_existing_workers_observe_live_policy_without_restart(self, tmp_path):
        from app.db.migrate import run_migrations
        from tests.e2e._processes import worker_roles

        environment = vault_environment(
            tmp_path, fresh_postgres_database("derivative_split")
        )
        environment["VAULT_SHARED_STORAGE"] = "true"
        run_migrations(environment["VAULT_DB_URL"])
        invoke(environment, "configure")
        worker = subprocess.Popen(
            [sys.executable, "-m", "app.worker"],
            cwd=BACKEND_DIR,
            env={**environment, "VAULT_PROCESS_ROLE": "worker"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        import time

        api = None
        try:
            deadline = time.monotonic() + 60
            while "worker" not in worker_roles(environment):
                assert time.monotonic() < deadline
                assert worker.poll() is None
                time.sleep(0.1)
            api = subprocess.Popen(
                [sys.executable, "-m", ROLE, "split"],
                cwd=BACKEND_DIR,
                env={
                    **environment,
                    "VAULT_PROCESS_ROLE": "api",
                    "VAULT_API_RUNS_JOBS": "false",
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert read_line(api)["phase"] == "disabled"
            assert worker.poll() is None
            api.stdin.write("enable\n")
            api.stdin.flush()
            outcome = read_line(api)
            assert outcome["phase"] == "enabled"
            assert set(outcome["states"].values()) == {"ready"}
            api.wait(timeout=30)
            assert api.returncode == 0
            assert worker.poll() is None
        finally:
            for process in [api, worker]:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=30)
