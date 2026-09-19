#!/usr/bin/env python3
"""Measure the existing durable queue repositories, including a real worker kill.

This diagnostic never executes an import payload. Full import correctness and API
latency belong to bench_import; this measures acceptance/claim/completion SQL and
recovery at the production lease duration without changing the stored deadlines.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bench_database import database_record, disposable_database


def database_bytes(engine) -> int:
    """Return committed database bytes without depending on provider tooling."""
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            pages = connection.exec_driver_sql("PRAGMA page_count").scalar_one()
            page_size = connection.exec_driver_sql("PRAGMA page_size").scalar_one()
            value = pages * page_size
        elif connection.dialect.name == "postgresql":
            value = connection.exec_driver_sql(
                "SELECT pg_database_size(current_database())"
            ).scalar_one()
        else:
            raise ValueError(
                f"Unsupported queue benchmark database: {connection.dialect.name}"
            )
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("database size inspection returned an invalid value")
    return value


def _require_owned_database() -> None:
    owner = os.environ.get("PRINTSTASH_QUEUE_BENCHMARK_OWNER")
    database_url = os.environ.get("VAULT_DB_URL")
    if not owner or not database_url:
        raise RuntimeError(
            "internal queue mode requires the disposable-database supervisor"
        )
    expected = hashlib.sha256(database_url.encode()).hexdigest()
    if Path(owner).read_text() != expected:
        raise RuntimeError("queue benchmark database ownership does not match")


def _claim_worker() -> None:
    _require_owned_database()
    from app.db.session import get_session_factory
    from app.modules.ingestion.commands import claim_next

    with get_session_factory().scoped_session() as session:
        claim = claim_next(session)
    if claim is None:
        raise RuntimeError("recovery worker found no accepted job")
    print(json.dumps(asdict(claim)), flush=True)
    # The parent deliberately terminates this process after the durable claim.
    # EOF is also a bounded ownership signal when the supervisor exits normally.
    sys.stdin.buffer.read(1)


def _latencies(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(values),
        "p50_ms": ordered[len(values) // 2] * 1000,
        "p95_ms": ordered[min(len(values) - 1, int(len(values) * 0.95))] * 1000,
        "max_ms": ordered[-1] * 1000,
    }


def _measure(count: int, idle_seconds: float, *, include_recovery: bool) -> dict:
    _require_owned_database()
    from sqlalchemy import func
    from sqlmodel import select

    from app.db.models import BackgroundJob
    from app.db.session import get_session_factory
    from app.modules.ingestion import commands
    from app.runtime.jobs import JobRegistry

    sessions = get_session_factory()
    registry = JobRegistry()
    accepted, claimed = set(), set()
    accepted_at = {}
    acceptance, claims, enqueue_to_start, completion = [], [], [], []
    started = time.monotonic()
    initial_cpu = time.process_time()

    def enqueue():
        with sessions.scoped_session() as session:
            job_id = registry.create(kind="model", session=session)
            commands.enqueue(
                session, job_id, "artifact", {"filename": "queue-benchmark.stl"}
            )
            session.commit()
            return job_id

    with sessions.scoped_session() as session:
        database = database_record(session.connection())
        rolled_back = registry.create(kind="model", session=session)
        commands.enqueue(session, rolled_back, "artifact", {})
        session.rollback()
        if session.get(BackgroundJob, rolled_back) is not None:
            raise RuntimeError("rolled-back acceptance survived")

    for _ in range(count):
        begin = time.monotonic()
        job_id = enqueue()
        accepted.add(job_id)
        accepted_at[job_id] = begin
        acceptance.append(time.monotonic() - begin)
    for _ in range(count):
        begin = time.monotonic()
        with sessions.scoped_session() as session:
            claim = commands.claim_next(session)
        claims.append(time.monotonic() - begin)
        if claim is None or claim.job_id not in accepted or claim.job_id in claimed:
            raise RuntimeError("accepted jobs were lost or claimed twice")
        claimed.add(claim.job_id)
        enqueue_to_start.append(time.monotonic() - accepted_at[claim.job_id])
        begin = time.monotonic()
        with commands.execution_scope(claim):
            registry.finish(claim.job_id, state="completed")
        completion.append(time.monotonic() - begin)

    idle_start, idle_cpu, polls = time.monotonic(), time.process_time(), 0
    while time.monotonic() - idle_start < idle_seconds:
        with sessions.scoped_session() as session:
            if commands.claim_next(session) is not None:
                raise RuntimeError("completed work became executable")
        polls += 1
        time.sleep(0.25)
    idle = {
        "seconds": time.monotonic() - idle_start,
        "cpu_seconds": time.process_time() - idle_cpu,
        "polls": polls,
        "poll_interval_seconds": 0.25,
    }

    with sessions.scoped_session() as session:
        completed = session.exec(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.state == "completed")
        ).one()
        if completed != count:
            raise RuntimeError("steady-state queue completion count changed")
    report = {
        "measurement_protocol": "durable-queue-steady-v1",
        "scope": "queue repositories; no import payload execution or HTTP server",
        "database": database,
        "accepted_count": count,
        "completed_count": completed,
        "rollback_orphans": 0,
        "duplicate_claims": len(accepted) - len(claimed),
        "acceptance": _latencies(acceptance),
        "claim": _latencies(claims),
        "enqueue_to_start": _latencies(enqueue_to_start),
        "completion": _latencies(completion),
        "idle": idle,
        "fault_injection": "not_run",
        "total_seconds": time.monotonic() - started,
        "coordinator_cpu_seconds": time.process_time() - initial_cpu,
    }
    if not include_recovery:
        return report

    recovery_id = enqueue()
    worker = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--claim-worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(worker.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=30):
                raise TimeoutError("recovery worker did not claim within 30 seconds")
            line = worker.stdout.readline()
        previous = commands.CommandClaim(**json.loads(line))
        if previous.job_id != recovery_id:
            raise RuntimeError("worker claimed the wrong recovery job")
        killed_at = time.monotonic()
        worker.kill()
        worker.wait(timeout=10)
        deadline = killed_at + commands.LEASE_SECONDS + 30
        current = None
        while time.monotonic() < deadline:
            with sessions.scoped_session() as session:
                current = commands.claim_next(session)
            if current is not None:
                break
            time.sleep(0.25)
        recovered_at = time.monotonic()
        if (
            current is None
            or current.job_id != recovery_id
            or current.token == previous.token
        ):
            raise RuntimeError("terminated worker's job did not recover")
        with sessions.scoped_session() as session:
            if commands.renew(session, previous):
                raise RuntimeError("stale worker renewed a successor's claim")
            commands.release(session, previous)
            row = session.get(BackgroundJob, recovery_id, populate_existing=True)
            if row is None or row.claim_token != current.token:
                raise RuntimeError("stale release changed the successor's claim")
        try:
            with commands.execution_scope(previous):
                registry.finish(
                    recovery_id, state="failed", error="stale benchmark worker"
                )
        except RuntimeError as error:
            if str(error) != "ingestion_claim_lost":
                raise
        else:
            raise RuntimeError("stale completion was accepted")
        with commands.execution_scope(current):
            registry.finish(recovery_id, state="completed")
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)
        if worker.stdin is not None:
            worker.stdin.close()
        if worker.stdout is not None:
            worker.stdout.close()

    with sessions.scoped_session() as session:
        terminal = session.exec(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.state == "completed")
        ).one()
        recovered = session.get(BackgroundJob, recovery_id)
        if terminal != count + 1 or recovered is None or recovered.attempts != 2:
            raise RuntimeError("queue completion identities or attempts changed")
    return {
        **report,
        "measurement_protocol": "durable-queue-baseline-v1",
        "accepted_count": count + 1,
        "completed_count": terminal,
        "fault_injection": "passed",
        "stale_completion_rejected": True,
        "production_lease_seconds": commands.LEASE_SECONDS,
        "terminated_worker_exit_code": worker.returncode,
        "recovery_seconds": recovered_at - killed_at,
        "total_seconds": time.monotonic() - started,
        "coordinator_cpu_seconds": time.process_time() - initial_cpu,
    }


def run(
    output: Path,
    *,
    database: str,
    count: int = 128,
    idle_seconds: float = 10,
    postgres_admin_url: str | None = None,
    include_recovery: bool = True,
) -> dict:
    if not 1 <= count <= 1024 or not 1 <= idle_seconds <= 60:
        raise ValueError("queue benchmark workload exceeds its bounds")
    backend = Path(__file__).resolve().parents[1]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="printstash-queue-bench-") as temporary:
        root = Path(temporary)
        with disposable_database(
            root, database, postgres_admin_url=postgres_admin_url
        ) as engine:
            database_bytes_before = database_bytes(engine)
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("VAULT_")
            }
            env.update(
                {
                    "PYTHONPATH": str(backend),
                    "VAULT_DB_URL": engine.url.render_as_string(hide_password=False),
                    "VAULT_SECRETS_KEY_FILE": str(root / "secrets.key"),
                    "VAULT_ARTIFACT_CACHE_ROOT": str(root / "cache"),
                }
            )
            owner = root / "database-owner"
            owner.write_text(hashlib.sha256(env["VAULT_DB_URL"].encode()).hexdigest())
            owner.chmod(0o600)
            env["PRINTSTASH_QUEUE_BENCHMARK_OWNER"] = str(owner)
            for name in ("DATA", "THUMB", "STAGING", "BACKUP"):
                folder = root / name.lower()
                folder.mkdir()
                env[f"VAULT_{name}_DIR"] = str(folder)
            with output.with_suffix(".server.log").open("w") as log:
                subprocess.run(
                    [sys.executable, "-m", "app.db.migrate"],
                    cwd=backend,
                    env=env,
                    stdout=log,
                    stderr=log,
                    check=True,
                    timeout=120,
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--measure",
                        "--jobs",
                        str(count),
                        "--idle-seconds",
                        str(idle_seconds),
                        *([] if include_recovery else ["--steady-state"]),
                    ],
                    cwd=backend,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    check=True,
                    timeout=600,
                )
            report = json.loads(result.stdout)
            database_growth = database_bytes(engine) - database_bytes_before
            if database_growth < 0:
                raise RuntimeError("queue benchmark database shrank during measurement")
            report["database_growth_bytes"] = database_growth
            output.write_text(json.dumps(report, indent=2) + "\n")
            return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--database", choices=("sqlite", "postgres"), default="sqlite")
    parser.add_argument("--jobs", type=int, default=128)
    parser.add_argument("--idle-seconds", type=float, default=10)
    parser.add_argument("--postgres-admin-url-env")
    parser.add_argument(
        "--steady-state",
        action="store_true",
        help="Measure ordinary queue operations only; report fault injection as not run",
    )
    parser.add_argument("--claim-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--measure", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.claim_worker:
        _claim_worker()
        return
    if not 1 <= args.jobs <= 1024 or not 1 <= args.idle_seconds <= 60:
        parser.error("workload exceeds its bounds")
    if args.measure:
        # App startup configures logging on stdout. Keep that operational output
        # in the supervisor's log, leaving this pipe for one structured result.
        with redirect_stdout(sys.stderr):
            report = _measure(
                args.jobs, args.idle_seconds, include_recovery=not args.steady_state
            )
        print(json.dumps(report), flush=True)
        return
    if args.output is None:
        parser.error("--output is required")
    admin = None
    if args.postgres_admin_url_env:
        admin = os.environ.get(args.postgres_admin_url_env)
        if not admin or args.database != "postgres":
            parser.error(
                "PostgreSQL requires a configured maintenance URL environment variable"
            )
    run(
        args.output,
        database=args.database,
        count=args.jobs,
        idle_seconds=args.idle_seconds,
        postgres_admin_url=admin,
        include_recovery=not args.steady_state,
    )


if __name__ == "__main__":
    main()
