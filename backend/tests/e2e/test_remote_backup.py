"""A remote-only backup remains recoverable through the public HTTP API.

The archive lives in a real S3-compatible or SFTP service and no local copy is retained;
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
from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_providers import SFTPProviderConfig, resolve_transport
from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, openssh_endpoint, s3_endpoint
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


@pytest.fixture(
    params=[
        pytest.param("s3", marks=pytest.mark.s3),
        pytest.param("sftp", marks=pytest.mark.remote_storage),
    ]
)
def remote_backup_profile(request):
    if request.param == "s3":
        endpoint, bucket = request.getfixturevalue("remote_backup_bucket")
        return {
            "kind": "s3",
            "configuration": {
                "provider": "s3_self_hosted",
                "bucket": bucket,
                "endpoint_url": endpoint,
                "region": "us-east-1",
                "root": "off-site",
                "addressing_style": "path",
            },
            "secrets": {"access_key": S3_ACCESS_KEY, "secret_key": S3_SECRET_KEY},
        }
    host, port, host_key = openssh_endpoint()
    configuration = {
        "provider": "sftp",
        "host": host,
        "port": port,
        "username": "contract",
        "host_key": host_key,
        "root": f"api-backup-{uuid4().hex}",
    }
    # Provision only this disposable test directory before enrolling the
    # profile. Runtime probes and reads must never create an absent root.
    backend = OpenDALStorageBackend(
        resolve_transport(SFTPProviderConfig(**configuration, password="contract-only"))
    )
    backend.provision_root()
    return {
        "kind": "sftp",
        "configuration": configuration,
        "secrets": {"password": "contract-only"},
    }


class TestRemoteBackup:
    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_remote_only_backup_restores_through_the_public_api(
        self, api, tmp_path, remote_backup_profile
    ) -> None:
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
                "purpose": "backup",
                **remote_backup_profile,
            },
        )
        assert connection.status_code == 201, connection.text
        probe = await api.post(
            f"/api/v1/storage-connections/{connection.json()['id']}/probe",
            headers=headers,
        )
        assert probe.status_code == 200, probe.text
        assert probe.json()["read"] is True

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
        assert metadata["location"] == f"opendal:{remote_backup_profile['kind']}"
        assert not list(Path(settings.backup_dir).glob("*.tar.gz"))
        listed = await api.get("/api/v1/backups/sources", headers=headers)
        assert listed.status_code == 200, listed.text
        assert any(
            item["source_ref"] == metadata["source_ref"] for item in listed.json()
        )
        verified = await api.post(
            f"/api/v1/backups/{metadata['backup_id']}/verify",
            headers=headers,
            params={"source_ref": metadata["source_ref"]},
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["valid"] is True
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
