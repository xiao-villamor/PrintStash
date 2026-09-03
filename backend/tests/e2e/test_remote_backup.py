"""A remote-only backup remains recoverable through the public HTTP API.

The archive lives in a real S3-compatible service and no local copy is retained;
the test then destroys the catalog row and Artifact bytes before restoring them
through the same operator-facing endpoints.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config as BotoConfig

from app.core.config import settings
from app.services.setup_token import current_setup_token
from app.services.storage_backend import init_backend
from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint
from tests.paths import FIXTURES_DIR

FIXTURE = FIXTURES_DIR / "sample.gcode"


@pytest.fixture
def remote_backup_bucket():
    endpoint = s3_endpoint()
    bucket = f"critical-api-backup-{uuid4().hex[:12]}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=BotoConfig(s3={"addressing_style": "path"}),
    )
    client.create_bucket(Bucket=bucket)
    try:
        yield endpoint, bucket
    finally:
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=item["Key"])
        client.delete_bucket(Bucket=bucket)


class TestRemoteBackup:
    @pytest.mark.critical
    @pytest.mark.s3
    @pytest.mark.asyncio
    async def test_remote_only_backup_restores_through_the_public_api(
        self, api, tmp_path, remote_backup_bucket
    ) -> None:
        endpoint, bucket = remote_backup_bucket
        setup = await api.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "owner",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(tmp_path / "files"),
                "thumb_dir": str(tmp_path / "thumbs"),
            },
        )
        assert setup.status_code == 201, setup.text
        init_backend()
        headers = {"Authorization": f"Bearer {setup.json()['access_token']}"}
        configured = await api.put(
            "/api/v1/config",
            headers=headers,
            json={"manual_local_backup_enabled": False},
        )
        assert configured.status_code == 200, configured.text
        connection = await api.post(
            "/api/v1/storage-connections",
            headers=headers,
            json={
                "name": f"critical backup {uuid4().hex}",
                "kind": "s3",
                "purpose": "backup",
                "configuration": {
                    "provider": "s3_self_hosted",
                    "bucket": bucket,
                    "endpoint_url": endpoint,
                    "region": "us-east-1",
                    "root": "off-site",
                    "addressing_style": "path",
                },
                "secrets": {
                    "access_key": S3_ACCESS_KEY,
                    "secret_key": S3_SECRET_KEY,
                },
            },
        )
        assert connection.status_code == 201, connection.text

        uploaded = await api.post(
            "/api/v1/ingest/orca",
            headers=headers,
            files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
            data={"model_name": "Remote recovery"},
        )
        assert uploaded.status_code == 202, uploaded.text
        job_id = uploaded.json()["job_id"]
        for _ in range(50):
            job = (
                await api.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
            ).json()
            if job["state"] in {"completed", "failed", "duplicate"}:
                break
            await asyncio.sleep(0.05)
        assert job["state"] == "completed", job
        model = next(
            item
            for item in (await api.get("/api/v1/models", headers=headers)).json()
            if item["name"] == "Remote recovery"
        )
        detail = (
            await api.get(f"/api/v1/models/{model['id']}", headers=headers)
        ).json()
        file_id = detail["files"][0]["id"]
        created = await api.post("/api/v1/backups", headers=headers)

        assert created.status_code == 202, created.text
        metadata = created.json()
        assert metadata["location"] == "opendal:s3"
        assert not list(Path(settings.backup_dir).glob("*.tar.gz"))
        trashed = await api.delete(f"/api/v1/models/{model['id']}", headers=headers)
        assert trashed.status_code == 204, trashed.text
        purged = await api.delete(
            f"/api/v1/models/{model['id']}/purge", headers=headers
        )
        assert purged.status_code == 200, purged.text
        assert (
            await api.get(f"/api/v1/models/{model['id']}", headers=headers)
        ).status_code == 404

        restored = await api.post(
            f"/api/v1/backups/{metadata['backup_id']}/restore",
            headers=headers,
            params={"source_ref": metadata["source_ref"]},
        )

        assert restored.status_code == 200, restored.text
        recovered = await api.get(f"/api/v1/files/{file_id}/download", headers=headers)
        assert recovered.status_code == 200, recovered.text
        assert recovered.content == FIXTURE.read_bytes()
        assert not list(Path(settings.backup_dir).glob("*.tar.gz"))
