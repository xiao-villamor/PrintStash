"""E2E: publish an artifact through the real API into loopback WebDAV."""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from sqlmodel import select

from app.db.models import File, OwnedStorageObject, StorageObjectState
from app.services.setup_token import current_setup_token

pytestmark = pytest.mark.e2e

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "real_orca_ender3_benchy.gcode"
)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture
def webdav_endpoint(tmp_path: Path):
    executable = shutil.which("wsgidav")
    if executable is None:
        pytest.fail("WsgiDAV E2E dependency is not installed; install the dev extra")
    port = _free_port()
    remote_root = tmp_path / "webdav"
    remote_root.mkdir()
    process = subprocess.Popen(
        [
            executable,
            "--host=127.0.0.1",
            f"--port={port}",
            f"--root={remote_root}",
            "--auth=anonymous",
            "--no-config",
            "--quiet",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    endpoint = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("WsgiDAV contract server exited during startup")
            try:
                urlopen(endpoint, timeout=0.2).close()  # noqa: S310
                break
            except Exception:
                time.sleep(0.05)
        else:
            pytest.fail("WsgiDAV contract server did not become ready")
        yield endpoint
    finally:
        process.terminate()
        process.wait(timeout=5)


async def _await_job(api, headers: dict[str, str], job_id: str) -> dict:
    for _ in range(100):
        response = await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["state"] in {"completed", "failed", "duplicate"}:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.mark.asyncio
class TestArtifactUpload:
    async def test_artifact_upload_is_committed_through_webdav(
        self, api, tmp_path: Path, e2e_db, webdav_endpoint: str
    ) -> None:
        setup = await api.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "owner",
                "password": "Password123",
                "storage_provider": "webdav",
                "storage_provider_config": {
                    "provider": "webdav",
                    "endpoint_url": webdav_endpoint,
                    "username": "webdav-user",
                    "password": "webdav-password",
                    "root": "vault-data",
                },
            },
        )
        assert setup.status_code == 201, setup.text
        headers = {"Authorization": f"Bearer {setup.json()['access_token']}"}

        # Use the production composition root to activate the persisted
        # provider; the E2E must not bypass configuration with bind_backend.
        from app.main import _compose_storage_backend
        from app.services import runtime_config

        runtime_config.apply_overlay(e2e_db)
        backend = _compose_storage_backend(recover_publications=False)

        health = await api.get("/api/v1/health")
        assert health.status_code == 200, health.text
        assert health.json()["storage"]["provider"] == "webdav"
        assert health.json()["storage"]["tier"] == "guarded"

        upload = await api.post(
            "/api/v1/ingest/orca",
            files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
            data={"model_name": "WebDAV Benchy"},
            headers=headers,
        )
        assert upload.status_code == 202, upload.text
        job = await _await_job(api, headers, upload.json()["job_id"])
        assert job["state"] == "completed", job

        artifact = e2e_db.exec(select(File)).one()
        assert backend.read_bytes(artifact.path) == FIXTURE.read_bytes()
        ownership = e2e_db.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == artifact.path)
        ).one()
        assert ownership.state == StorageObjectState.COMMITTED

        trashed = await api.delete(
            f"/api/v1/models/{artifact.model_id}", headers=headers
        )
        assert trashed.status_code == 204, trashed.text
        rejected = await api.delete(
            f"/api/v1/models/{artifact.model_id}/purge", headers=headers
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "storage_risk_confirmation_required"
        assert backend.exists(artifact.path)
