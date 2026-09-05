"""Partial backup recovery through real HTTP routes and loopback WebDAV."""

import hashlib
from pathlib import Path

import pytest

from tests.e2e._backup_helpers import setup_and_login


class TestReplicaRunRecovery:
    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_partial_run_retries_the_same_archive_through_the_api(
        self, api, tmp_path, webdav_endpoint
    ):
        headers = await setup_and_login(api, tmp_path)
        obstruction = tmp_path / "webdav" / "replicas"
        obstruction.write_bytes(b"prevent creating the backup directory")
        connection = await api.post(
            "/api/v1/storage-connections",
            headers=headers,
            json={
                "name": "Temporarily unavailable replica",
                "kind": "webdav",
                "purpose": "backup",
                "configuration": {
                    "provider": "webdav",
                    "endpoint_url": webdav_endpoint,
                    "root": "replicas",
                    "username": "backup-user",
                },
                "secrets": {"password": "backup-password"},
            },
        )
        assert connection.status_code == 201, connection.text
        created = await api.post("/api/v1/backups", headers=headers)
        assert created.status_code == 202, created.text
        body = created.json()
        assert body["outcome"] == "partial"
        failed = next(
            row for row in body["destination_results"] if row["kind"] == "connection"
        )
        local = next(
            row for row in body["destination_results"] if row["kind"] == "local"
        )
        assert local["outcome"] == "completed"
        assert failed["outcome"] == "failed"
        obstruction.unlink()
        response = await api.post(
            f"/api/v1/backups/runs/destinations/{failed['id']}/retry", headers=headers
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["outcome"] == "completed"
        assert result["retry_attempts"][0]["source_result_id"] == local["id"]
        archives = list((tmp_path / "webdav").rglob("*.tar.gz"))
        assert len(archives) == 1
        assert (
            hashlib.sha256(archives[0].read_bytes()).hexdigest()
            == body["archive_sha256"]
        )
        assert archives[0].read_bytes() == Path(local["key"]).read_bytes()
        detail = await api.get(
            f"/api/v1/backups/runs/{body['run_id']}", headers=headers
        )
        assert detail.json()["outcome"] == "completed"
        assert detail.json()["backup_id"] == body["backup_id"]
