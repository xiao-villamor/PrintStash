"""Real application roles for durable derivative-policy tests."""

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import ensure_dirs
from app.db.models import (
    ArtifactDerivative,
    DerivativeState,
    File,
    Job,
    JobKind,
    JobState,
)
from app.db.session import get_session_factory
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import bind_backend, get_backend
from tests.fakes.job_engine_process import (
    _DEADLINE_S,
    _STL,
    _emit,
    _follow,
    _set_up,
    _settle,
)


def _upload_disabled(client):
    response = client.put("/api/v1/config", json={"derivatives_mesh_enabled": False})
    assert response.status_code == 200, response.text
    uploaded = client.post(
        "/api/v1/ingest/model", files={"file": ("policy.stl", _STL, "application/sla")}
    )
    assert uploaded.status_code == 202, uploaded.text
    job = _follow(client, uploaded.json()["job_id"])
    assert job["state"] == "completed", job
    file_id = job["file_id"]
    states = client.get(f"/api/v1/files/{file_id}/derivatives").json()
    assert {row["state"] for row in states} == {"disabled"}, states
    with get_session_factory().scoped_session() as session:
        assert (
            session.exec(
                select(Job.id).where(Job.kind == JobKind.DERIVATIVES_MESH)
            ).all()
            == []
        )
    return file_id


class StalledStorage(LocalStorageBackend):
    def __init__(self, original, marker):
        self.__dict__ = original.__dict__.copy()
        self.marker = marker

    @contextmanager
    def local_path(self, key, *, directory=None):
        with get_session_factory().scoped_session() as session:
            file = session.exec(select(File).where(File.path == key)).first()
            running = (
                file is not None
                and session.exec(
                    select(ArtifactDerivative.id).where(
                        ArtifactDerivative.file_id == file.id,
                        ArtifactDerivative.state == DerivativeState.RUNNING,
                    )
                ).first()
                is not None
            )
        if running:
            self.marker.write_text(str(file.id))
            while True:
                time.sleep(1)
        with super().local_path(key) as path:
            yield path


def main():
    from app.main import app

    role = sys.argv[1]
    ensure_dirs()
    with TestClient(app) as client:
        if role in {"disabled", "configure", "stall_storage"}:
            _set_up(client)
        if role == "configure":
            _emit(configured=True)
        if role == "split":
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "owner", "password": "Password123"},
            )
            assert login.status_code == 200, login.text
            client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        if role in {"disabled", "split"}:
            file_id = _upload_disabled(client)
            _emit(phase="disabled", file_id=file_id)
            if role == "split":
                assert sys.stdin.readline().strip() == "enable"
                enabled = client.put(
                    "/api/v1/config", json={"derivatives_mesh_enabled": True}
                )
                assert enabled.status_code == 200, enabled.text
                _emit(phase="enabled", **_settle(file_id))
        elif role == "read":
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "owner", "password": "Password123"},
            )
            assert login.status_code == 200, login.text
            client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            config = client.get("/api/v1/config")
            _emit(enabled=config.json()["derivatives_mesh_enabled"])
        elif role == "stall_storage":
            marker = Path(sys.argv[2])
            bind_backend(StalledStorage(get_backend(), marker))
            uploaded = client.post(
                "/api/v1/ingest/model",
                files={"file": ("recovery.stl", _STL, "application/sla")},
            )
            assert uploaded.status_code == 202, uploaded.text
            deadline = time.monotonic() + 60
            while not marker.exists():
                assert time.monotonic() < deadline
                time.sleep(0.05)
            _emit(file_id=int(marker.read_text()))
            while True:
                time.sleep(1)
        elif role == "recover_disabled":
            file_id = int(sys.argv[2])
            # A crashed reconcile pass can retain its cursor claim for 60s;
            # settlement may need the next tick after that lease expires.
            # Use the same bounded budget as the other real DBOS roles.
            deadline = time.monotonic() + _DEADLINE_S
            while True:
                with get_session_factory().scoped_session() as session:
                    jobs = session.exec(
                        select(Job).where(Job.kind == JobKind.DERIVATIVES_MESH)
                    ).all()
                    if jobs and all(row.state is JobState.CANCELLED for row in jobs):
                        rows = session.exec(
                            select(ArtifactDerivative).where(
                                ArtifactDerivative.file_id == file_id
                            )
                        ).all()
                        if not any(
                            row.state is DerivativeState.RUNNING for row in rows
                        ):
                            _emit(
                                states={row.kind: row.state.value for row in rows},
                                attempts={row.kind: row.attempts for row in rows},
                                errors=[
                                    json.loads(row.status_json).get("error")
                                    for row in jobs
                                ],
                            )
                            return
                assert time.monotonic() < deadline
                time.sleep(0.1)


if __name__ == "__main__":
    import faulthandler

    faulthandler.dump_traceback_later(150, exit=True)
    main()
