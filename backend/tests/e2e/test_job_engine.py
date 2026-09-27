"""Background work survives the process that was running it, on real DBOS.

Every other E2E flow runs the inline engine so it can assert settled state
deterministically. These tests boot the production composition in child
processes, on the durable engine, and kill one mid-step:

- a process dies while a derivative is running; a restarted process finds the
  execution stranded on a dead executor, interrupts it, and runs it again to
  completion;
- a process of the next application version starts on a vault whose previous
  version died mid-step; it cancels the old version's execution immediately
  and reruns the work on its own code;
- a restore discards the engine's state mid-derivative; the next process
  rebuilds the owed work from the vault alone;
- the next build bumps a thumbnail recipe; every Artifact is re-derived in
  the background while its current thumbnail stays visible.

All run on SQLite and on PostgreSQL, the two supported databases, because the
engine keeps its state in a different place on each (a sibling file, or the
``dbos`` schema of the application database).
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, text

from app.db.url import normalize_database_url
from app.runtime.engine.dbos_engine import system_database_url
from tests.containers import fresh_postgres_database
from tests.e2e._processes import vault_environment
from tests.paths import BACKEND_DIR

_ROLE = "tests.fakes.job_engine_process"


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def vault_env(request, tmp_path: Path) -> dict[str, str]:
    db_url = (
        fresh_postgres_database("job_engine")
        if request.param == "postgresql"
        else f"sqlite:///{tmp_path / 'vault.sqlite'}"
    )
    return vault_environment(tmp_path, db_url)


def _stall(environment: dict[str, str], marker: Path) -> tuple[subprocess.Popen, int]:
    process, held = _start(environment, "stall", str(marker))
    return process, held["file_id"]


def _start(environment: dict[str, str], *role: str) -> tuple[subprocess.Popen, dict]:
    """Start a role that holds work mid-step; its first JSON line describes it.

    Its log goes to a file, not a pipe: nobody reads stderr while the role
    runs, and a full pipe would block the child on its next log line.
    """
    log = tempfile.TemporaryFile(mode="w+")
    process = subprocess.Popen(
        [sys.executable, "-m", _ROLE, *role],
        cwd=BACKEND_DIR,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=log,
        text=True,
    )
    assert process.stdout is not None
    deadline = time.monotonic() + 120
    for line in process.stdout:
        if line.startswith("{"):
            return process, json.loads(line)
        assert time.monotonic() < deadline
    process.wait(timeout=10)
    log.seek(0)
    raise AssertionError(f"stalling process exited early:\n{log.read()[-6000:]}")


def _kill(process: subprocess.Popen) -> None:
    # SIGKILL: no shutdown hook runs, exactly like a crash or an OOM kill.
    process.send_signal(signal.SIGKILL)
    process.wait(timeout=30)


def _run(environment: dict[str, str], *role: str) -> dict:
    """Run one role to completion; its outcome, once every derivative is ready."""
    result = subprocess.run(
        [sys.executable, "-m", _ROLE, *role],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    assert lines, result.stdout + result.stderr
    outcome = json.loads(lines[-1])
    if set(outcome["states"].values()) != {"ready"}:
        # pytest.fail keeps the whole message; an assertion repr would cut
        # the diagnostics that say why the work never converged.
        pytest.fail(
            f"did not converge:\n{json.dumps(outcome, indent=1)}\n"
            f"{(result.stdout + result.stderr)[-6000:]}"
        )
    return outcome


def _converge(environment: dict[str, str], file_id: int) -> dict[str, str]:
    return _run(environment, "converge", str(file_id))["states"]


def _discard_engine_state(environment: dict[str, str]) -> None:
    """What a restore does: the engine's state goes, the vault's stays."""
    db_url = environment["VAULT_DB_URL"]
    url, schema = system_database_url(db_url)
    if schema is None:
        sibling = Path(make_url(url).database or "")
        assert sibling.exists(), "the engine never wrote its state"
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{sibling}{suffix}").unlink(missing_ok=True)
        return
    engine = create_engine(normalize_database_url(db_url))
    try:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        engine.dispose()


class TestCrashRecovery:
    @pytest.mark.critical
    def test_work_on_a_dead_executor_is_rerun_by_a_restarted_process(
        self, vault_env: dict[str, str], tmp_path: Path
    ) -> None:
        process, file_id = _stall(vault_env, tmp_path / "running")
        _kill(process)

        states = _converge(vault_env, file_id)

        assert states == {"metadata": "ready", "thumbnail": "ready"}

    def test_a_restarted_api_reruns_its_predecessors_work_at_once(
        self, vault_env: dict[str, str], tmp_path: Path
    ) -> None:
        # One API per vault: holding the vault lock proves the previous API
        # process is gone, so its work must not wait out the stale window
        # (here an hour, far beyond the harness deadline).
        process, file_id = _stall(vault_env, tmp_path / "running")
        _kill(process)
        restarted = {
            **vault_env,
            "VAULT_JOBS_EXECUTOR_STALE_SECONDS": "3600",
            "JOB_ENGINE_DEADLINE_S": "60",
        }

        states = _converge(restarted, file_id)

        assert states == {"metadata": "ready", "thumbnail": "ready"}

    def test_a_new_version_reruns_what_the_old_one_left_running(
        self, vault_env: dict[str, str], tmp_path: Path
    ) -> None:
        # An upgrade does not wait for the old executor to go stale: work of
        # another version is cancelled at startup and rerun on the new code.
        old = {**vault_env, "VAULT_APP_VERSION": "0.0.1-old"}
        process, file_id = _stall(old, tmp_path / "running")
        _kill(process)
        new = {
            **vault_env,
            "VAULT_APP_VERSION": "0.0.2-new",
            # Long enough that only the version sweep can explain recovery;
            # the harness deadline stays generous so a loaded runner is not
            # mistaken for a missed sweep.
            "VAULT_JOBS_EXECUTOR_STALE_SECONDS": "3600",
        }

        states = _converge(new, file_id)

        assert states == {"metadata": "ready", "thumbnail": "ready"}


class TestEngineStateLoss:
    @pytest.mark.critical
    def test_work_converges_after_the_engine_state_is_discarded(
        self, vault_env: dict[str, str], tmp_path: Path
    ) -> None:
        # A restore discards the engine's state; everything it knew about the
        # interrupted derivative is gone, and only the vault says it is owed.
        process, file_id = _stall(vault_env, tmp_path / "running")
        _kill(process)
        _discard_engine_state(vault_env)

        states = _converge(vault_env, file_id)

        assert states == {"metadata": "ready", "thumbnail": "ready"}


def _lose_artifact_bytes(environment: dict[str, str], file_id: int) -> None:
    """The disaster a restore recovers from: the Artifact's bytes are gone."""
    engine = create_engine(normalize_database_url(environment["VAULT_DB_URL"]))
    try:
        with engine.connect() as connection:
            key = connection.execute(
                text("SELECT path FROM files WHERE id = :id"), {"id": file_id}
            ).scalar_one()
    finally:
        engine.dispose()
    blob = Path(key)
    if not blob.is_absolute():
        blob = Path(environment["VAULT_DATA_DIR"]) / key
    blob.unlink()


class TestRestore:
    """A backup restored through the API, on a real engine that starts empty.

    SQLite only: PrintStash backs up a SQLite vault itself, while a PostgreSQL
    vault is restored by the operator's own database tooling, which also
    brings back (or drops) the engine's ``dbos`` schema. That case, a restored
    application database with an empty engine schema, is
    ``TestEngineStateLoss[postgresql]``.
    """

    @pytest.mark.critical
    def test_a_restored_vault_heals_work_its_engine_never_knew(
        self, tmp_path: Path
    ) -> None:
        # The backup's database says the derivative is running; the engine a
        # restore starts with knows nothing about it. Only the reconciler,
        # reading the restored database, can bring the work back.
        vault_env = vault_environment(
            tmp_path, f"sqlite:///{tmp_path / 'vault.sqlite'}"
        )
        process, held = _start(vault_env, "stall_backup", str(tmp_path / "running"))
        _kill(process)
        _discard_engine_state(vault_env)
        _lose_artifact_bytes(vault_env, held["file_id"])

        outcome = _run(
            vault_env,
            "restore_converge",
            held["backup_id"],
            held["source_ref"],
            str(held["file_id"]),
        )

        assert outcome["states"] == {"metadata": "ready", "thumbnail": "ready"}


@pytest.fixture
def rederived(vault_env: dict[str, str]) -> dict:
    uploaded = _run(vault_env, "upload")
    outcome = _run(vault_env, "rederive", str(uploaded["file_id"]))
    return {"before": uploaded, "after": outcome}


class TestRecipeBump:
    def test_the_next_build_rederives_the_changed_kind(self, rederived: dict) -> None:
        before, after = rederived["before"]["recipes"], rederived["after"]["recipes"]

        assert after["thumbnail"] == before["thumbnail"] + 1

    def test_the_old_thumbnail_stays_visible_while_rederiving(
        self, rederived: dict
    ) -> None:
        old = rederived["before"]["thumbnail"]

        assert old is not None
        assert rederived["after"]["shown_while_deriving"] == [old]

    def test_the_new_thumbnail_replaces_it(self, rederived: dict) -> None:
        assert rederived["after"]["thumbnail"] is not None
