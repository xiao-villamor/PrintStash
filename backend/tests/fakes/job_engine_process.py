"""One PrintStash process on the real DBOS engine, driven by ``tests/e2e``.

Each invocation boots the production composition (lifespan, DBOS, the
reconciler) against the vault the environment points at, performs one role,
and prints one JSON line the parent reads:

``stall``
    Set the vault up, upload a mesh, and hold its mesh derivative mid-step
    until the parent kills the process. Prints ``{"file_id": ...}`` once the
    derivative is running.
``converge``
    Boot on the same vault and wait for that Artifact's derivatives to settle,
    the way a restarted process recovers work a dead one left behind. Prints
    the derivative states.
``upload``
    Set the vault up, upload a mesh and wait for its derivatives to settle.
    Prints the Artifact's id, derivative states and thumbnail.
``rederive``
    Boot as a build whose mesh thumbnail recipe is one version newer, record
    the thumbnail the Artifact showed while the new one was being derived,
    and wait for the new recipe to settle.
``stall_backup``
    Like ``stall``, and take a backup while the derivative is held, so the
    archive's database records it running. Prints the backup's locator.
``restore_converge``
    Boot on an empty engine, restore that backup through the API, and wait
    for the restored Artifact's derivatives to settle.
``split_api``
    Serve as an API that runs no jobs (``VAULT_API_RUNS_JOBS=false``): set the
    vault up, open the events socket, upload a mesh and print
    ``{"job_id": ...}``; then wait for the workers the parent starts to finish
    it, and print the notices the socket received and the derivative states.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

_DEADLINE_S = float(os.environ.get("JOB_ENGINE_DEADLINE_S", "120"))
_STL = (
    b"solid cube\n"
    + b"".join(
        b"facet normal 0 0 1\nouter loop\nvertex %d 0 0\nvertex 0 %d 0\n"
        b"vertex 0 0 %d\nendloop\nendfacet\n" % (n, n, n)
        for n in range(1, 5)
    )
    + b"endsolid cube\n"
)


def _emit(**payload) -> None:
    print(json.dumps(payload), flush=True)


def _publish_marker(marker: Path, file_id: int) -> None:
    """Make the file visible only after its id has been written."""
    staged = marker.with_name(f".{marker.name}.tmp")
    staged.write_text(str(file_id))
    staged.replace(marker)


def _set_up(client: TestClient) -> None:
    """Complete first-run setup and authenticate ``client`` as the owner."""
    from app.core.config import settings

    client.headers["Origin"] = "http://testserver"
    csrf = client.post("/api/v1/setup/session").json()["csrf"]
    client.headers["X-PrintStash-Setup-CSRF"] = csrf
    setup = client.post(
        "/api/v1/setup",
        json={
            "username": "owner",
            "password": "Password123",
            "storage_backend": "local",
            "data_dir": str(settings.data_dir),
            "thumb_dir": str(settings.thumb_dir),
        },
    )
    assert setup.status_code == 201, setup.text
    client.headers["Authorization"] = f"Bearer {setup.json()['access_token']}"


def stall(marker: Path) -> None:
    from app.core.config import ensure_dirs
    from app.main import app
    from app.modules.derivatives import producers

    def held(file_id: int):
        _publish_marker(marker, file_id)
        while True:  # the parent kills this process here
            time.sleep(1)

    # Before the lifespan builds the catalog, which captures the producer.
    producers.derive_mesh = held
    ensure_dirs()
    with TestClient(app) as client:
        _set_up(client)
        uploaded = client.post(
            "/api/v1/ingest/model",
            files={"file": ("crash.stl", _STL, "application/sla")},
        )
        assert uploaded.status_code == 202, uploaded.text
        deadline = time.monotonic() + _DEADLINE_S
        while not marker.exists():
            assert time.monotonic() < deadline, "derivative never started"
            time.sleep(0.1)
        _emit(file_id=int(marker.read_text()))
        while True:
            time.sleep(1)


def _follow(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + _DEADLINE_S
    while True:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["state"] in {"completed", "failed", "cancelled"}:
            return job
        assert time.monotonic() < deadline, f"job never settled: {job}"
        time.sleep(0.25)


def stall_backup(marker: Path) -> None:
    """Back the vault up while its only Artifact's derivative is mid-step.

    The archive's database therefore records the derivative Job as running.
    Prints the backup's locator, then holds until the parent kills it.
    """
    from app.core.config import ensure_dirs
    from app.main import app
    from app.modules.derivatives import producers

    def held(file_id: int):
        _publish_marker(marker, file_id)
        while True:  # the parent kills this process here
            time.sleep(1)

    producers.derive_mesh = held
    ensure_dirs()
    with TestClient(app) as client:
        _set_up(client)
        uploaded = client.post(
            "/api/v1/ingest/model",
            files={"file": ("restored.stl", _STL, "application/sla")},
        )
        assert uploaded.status_code == 202, uploaded.text
        deadline = time.monotonic() + _DEADLINE_S
        while not marker.exists():
            assert time.monotonic() < deadline, "derivative never started"
            time.sleep(0.1)
        accepted = client.post("/api/v1/backups")
        assert accepted.status_code == 202, accepted.text
        backup = _follow(client, accepted.json()["job_id"])
        assert backup["state"] == "completed", backup
        _emit(
            file_id=int(marker.read_text()),
            backup_id=backup["result"]["backup_id"],
            source_ref=backup["result"]["source_ref"],
        )
        while True:
            time.sleep(1)


def restore_converge(backup_id: str, source_ref: str, file_id: int) -> None:
    """Boot on an empty engine, restore the backup, and wait for its work.

    Nothing in the engine knows the restored derivative: only the restored
    application database says it is owed, so the reconciler must heal it.
    """
    from app.core.config import ensure_dirs
    from app.main import app

    ensure_dirs()
    with TestClient(app) as client:
        client.headers["Origin"] = "http://testserver"
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "Password123"},
        )
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        restored = client.post(
            f"/api/v1/backups/{backup_id}/restore", params={"source_ref": source_ref}
        )
        assert restored.status_code == 200, restored.text
        outcome = _settle(file_id)
        from app.bootstrap.work import current
        from app.runtime import maintenance

        _emit(
            restored=True,
            jobs=_jobs(),
            maintenance=maintenance.restore_in_progress(),
            running_work=current() is not None,
            **outcome,
        )


def _jobs() -> list[list]:
    """Every Job row, for a failing assertion to show what the vault owed."""
    from sqlmodel import select

    from app.db.models import Job
    from app.db.session import get_session_factory

    with get_session_factory().scoped_session() as session:
        return [
            [job.kind, str(job.state), job.attempts, job.resubmits]
            for job in session.exec(select(Job)).all()
        ]


def _settle(file_id: int) -> dict:
    """Wait for the Artifact's current-recipe derivatives to settle."""
    from sqlmodel import select

    from app.db.models import ArtifactDerivative, DerivativeState, File
    from app.db.session import get_session_factory
    from app.modules.derivatives import kinds

    deadline = time.monotonic() + _DEADLINE_S
    while True:
        with get_session_factory().scoped_session() as session:
            file = session.get(File, file_id)
            assert file is not None
            current = kinds.recipes_for(file)
            rows = [
                row
                for row in session.exec(
                    select(ArtifactDerivative).where(
                        ArtifactDerivative.file_id == file_id
                    )
                ).all()
                if current.get(row.kind) == row.recipe_version
            ]
            states = {row.kind: DerivativeState(row.state).value for row in rows}
            outcome = {
                "states": states,
                "reasons": {row.kind: row.failure_reason for row in rows},
                "recipes": {row.kind: row.recipe_version for row in rows},
                "thumbnail": file.thumbnail_path,
            }
        settled = set(states) == set(current) and all(
            state in {"ready", "failed"} for state in states.values()
        )
        if settled:
            return outcome
        if time.monotonic() > deadline:
            # What the parent needs to tell a lost nudge from a wrong verdict.
            return {**outcome, "diagnostics": _diagnostics()}
        time.sleep(0.25)


def _diagnostics() -> dict:
    from sqlmodel import select

    from app.db.models import Job, ReconcileCursor, WorkExecutor
    from app.db.session import get_session_factory
    from app.modules.work.catalog import get_engine
    from app.modules.work.submission import execution_id

    with get_session_factory().scoped_session() as session:
        jobs = session.exec(select(Job)).all()
        ids = [execution_id(job.id, job.attempts) for job in jobs if job.attempts]
        evidence = get_engine().evidence(ids) if ids else {}
        return {
            "jobs": [
                [job.kind, str(job.state), job.attempts, job.subject_key]
                for job in jobs
            ],
            "evidence": {key: repr(value) for key, value in evidence.items()},
            "executors": [
                [row.executor_id, row.role, str(row.heartbeat_at)]
                for row in session.exec(select(WorkExecutor)).all()
            ],
            "cursors": [
                [
                    row.source,
                    str(row.nudged_at),
                    str(row.pass_queued_at),
                    str(row.last_pass_finished_at),
                    row.last_pass_submitted,
                    row.last_pass_deferred,
                ]
                for row in session.exec(select(ReconcileCursor)).all()
                if row.source.startswith(("derive", "ingest"))
            ],
        }


def converge(file_id: int) -> None:
    from app.core.config import ensure_dirs
    from app.main import app

    ensure_dirs()
    with TestClient(app):
        _emit(**_settle(file_id))


def upload() -> None:
    from app.core.config import ensure_dirs
    from app.main import app

    ensure_dirs()
    with TestClient(app) as client:
        _set_up(client)
        uploaded = client.post(
            "/api/v1/ingest/model",
            files={"file": ("recipe.stl", _STL, "application/sla")},
        )
        assert uploaded.status_code == 202, uploaded.text
        job_id = uploaded.json()["job_id"]
        deadline = time.monotonic() + _DEADLINE_S
        while (
            file_id := client.get(f"/api/v1/jobs/{job_id}").json().get("file_id")
        ) is None:
            assert time.monotonic() < deadline, "the upload never committed"
            time.sleep(0.25)
        _emit(file_id=file_id, **_settle(file_id))


def rederive(file_id: int) -> None:
    """Boot as the next build, whose thumbnail recipe changed.

    Records what the Artifact showed while the new thumbnail was being
    derived, then waits for the new recipe to settle.
    """
    from app.core.config import ensure_dirs
    from app.db.models import DerivativeKind, File, JobKind
    from app.db.session import get_session_factory
    from app.main import app
    from app.modules.derivatives import kinds, producers

    mesh = kinds.group(JobKind.DERIVATIVES_MESH)
    mesh.kinds[DerivativeKind.THUMBNAIL] = mesh.kinds[DerivativeKind.THUMBNAIL] + 1
    shown_while_deriving: list[str | None] = []
    original = producers.derive_mesh

    def observed(derived_id: int):
        with get_session_factory().scoped_session() as session:
            file = session.get(File, derived_id)
            shown_while_deriving.append(file.thumbnail_path if file else None)
        return original(derived_id)

    # Before the lifespan builds the catalog, which captures the producer.
    producers.derive_mesh = observed
    ensure_dirs()
    with TestClient(app):
        outcome = _settle(file_id)
    _emit(shown_while_deriving=shown_while_deriving, **outcome)


def split_api() -> None:
    from sqlmodel import select

    from app.core.config import ensure_dirs
    from app.db.models import ArtifactDerivative, DerivativeState
    from app.db.session import get_session_factory
    from app.main import app

    def give_up() -> None:
        # A notice that never arrives would block the socket read forever.
        print("split_api: no outcome before the deadline", file=sys.stderr)
        sys.stderr.flush()
        os._exit(3)

    watchdog = threading.Timer(_DEADLINE_S, give_up)
    watchdog.daemon = True
    watchdog.start()
    ensure_dirs()
    with TestClient(app) as client:
        _set_up(client)
        ticket = client.post("/api/v1/events/ticket").json()["ticket"]
        with client.websocket_connect(f"/api/v1/events/ws?ticket={ticket}") as ws:
            assert ws.receive_json() == {"type": "resync"}
            uploaded = client.post(
                "/api/v1/ingest/model",
                files={"file": ("split.stl", _STL, "application/sla")},
            )
            assert uploaded.status_code == 202, uploaded.text
            job_id = uploaded.json()["job_id"]
            _emit(job_id=job_id)
            # Only a worker can move this Job; each change reaches this socket
            # from another process, over NOTIFY.
            notices: list[dict] = []
            while True:
                notice = ws.receive_json()
                notices.append(notice)
                if notice.get("job_id") == job_id and notice.get("state") in {
                    "completed",
                    "failed",
                }:
                    break
        file_id = client.get(f"/api/v1/jobs/{job_id}").json()["file_id"]
        deadline = time.monotonic() + _DEADLINE_S
        while True:
            with get_session_factory().scoped_session() as session:
                states = {
                    row.kind: DerivativeState(row.state).value
                    for row in session.exec(
                        select(ArtifactDerivative).where(
                            ArtifactDerivative.file_id == file_id
                        )
                    ).all()
                }
            settled = states and all(
                state in {"ready", "failed"} for state in states.values()
            )
            if settled or time.monotonic() > deadline:
                break
            time.sleep(0.25)
        _emit(
            notices=[n for n in notices if n.get("job_id") == job_id],
            states=states,
        )


if __name__ == "__main__":
    import faulthandler

    # No role may hang the suite: past twice the deadline it prints every
    # thread's stack to stderr (which the parent shows) and exits.
    faulthandler.dump_traceback_later(_DEADLINE_S * 2, exit=True)
    role = sys.argv[1]
    if role == "stall":
        stall(Path(sys.argv[2]))
    elif role == "converge":
        converge(int(sys.argv[2]))
    elif role == "upload":
        upload()
    elif role == "stall_backup":
        stall_backup(Path(sys.argv[2]))
    elif role == "restore_converge":
        restore_converge(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif role == "rederive":
        rederive(int(sys.argv[2]))
    elif role == "split_api":
        split_api()
    else:
        raise SystemExit(f"unknown role {role}")
