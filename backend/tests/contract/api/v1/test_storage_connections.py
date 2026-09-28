"""Storage connection probes against a real loopback SFTP server."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.paths import BACKEND_DIR

pytestmark = pytest.mark.contract


@pytest.fixture
def sftp_library_endpoint(tmp_path: Path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    root = tmp_path / "server" / "models"
    root.mkdir(parents=True)
    (root / "part.stl").write_text("solid part\nendsolid part\n")
    known_hosts = tmp_path / "known-hosts"
    process = subprocess.Popen(
        [
            str(BACKEND_DIR / ".venv" / "bin" / "python"),
            "-m",
            "tests.fakes.mock_sftp",
            "--port",
            str(port),
            "--root",
            str(tmp_path / "server"),
            "--password",
            "contract-secret",
            "--known-hosts",
            str(known_hosts),
        ],
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        yield port, known_hosts
    finally:
        process.terminate()
        process.wait(timeout=5)


class TestStorageConnectionProbe:
    def test_shared_sftp_profile_lists_a_real_library_directory(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        sftp_library_endpoint: tuple[int, Path],
    ) -> None:
        port, known_hosts = sftp_library_endpoint
        created = client.post(
            "/api/v1/storage-connections",
            headers=auth_headers,
            json={
                "name": "Shared SFTP",
                "kind": "sftp",
                "purpose": "both",
                "configuration": {
                    "provider": "sftp",
                    "host": "127.0.0.1",
                    "port": port,
                    "username": "printstash",
                    "host_key": str(known_hosts),
                    "root": "models",
                },
                "secrets": {"password": "contract-secret"},
            },
        )
        assert created.status_code == 201, created.text

        response = client.post(
            f"/api/v1/storage-connections/{created.json()['id']}/probe",
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["library_sample_count"] == 1
