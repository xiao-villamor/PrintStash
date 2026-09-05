"""Real remote providers survive the complete public library-scan flow.

Listing alone is not enough: these cases configure the provider through the API,
run discovery, persist a linked Artifact, and download its bytes through the
public file endpoint against pinned Nextcloud and OpenSSH containers.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config as BotoConfig
from sqlmodel import select

from app.db.models import File, LibrarySourceKind
from app.services.storage_connections import parse_connection_config
from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_providers import resolve_transport
from tests.containers import (
    S3_ACCESS_KEY,
    S3_SECRET_KEY,
    nextcloud_endpoint,
    openssh_endpoint,
    s3_endpoint,
)
from tests.paths import FIXTURES_DIR


@dataclass(frozen=True)
class _RemoteProvider:
    kind: str
    configuration: dict[str, object]
    secrets: dict[str, str]
    backend: OpenDALStorageBackend


def _nextcloud_provider() -> _RemoteProvider:
    root = f"critical-nextcloud-{uuid4().hex}"
    configuration: dict[str, object] = {
        "provider": "nextcloud",
        "endpoint_url": nextcloud_endpoint(),
        "username": "admin",
        "root": root,
    }
    secrets = {"password": "contract-only"}
    parsed = parse_connection_config(LibrarySourceKind.WEBDAV, configuration, secrets)
    backend = OpenDALStorageBackend(resolve_transport(parsed))
    backend.ensure_setup()
    return _RemoteProvider("webdav", configuration, secrets, backend)


def _sftp_provider() -> _RemoteProvider:
    host, port, host_key = openssh_endpoint()
    root = f"critical-sftp-{uuid4().hex}"
    configuration: dict[str, object] = {
        "host": host,
        "port": port,
        "username": "contract",
        "host_key": host_key,
        "root": root,
    }
    secrets = {"password": "contract-only"}
    parsed = parse_connection_config(LibrarySourceKind.SFTP, configuration, secrets)
    backend = OpenDALStorageBackend(resolve_transport(parsed))
    backend.provision_root()
    backend.ensure_setup()
    return _RemoteProvider("sftp", configuration, secrets, backend)


def _s3_provider() -> _RemoteProvider:
    endpoint = s3_endpoint()
    bucket = f"source-markers-{uuid4().hex[:12]}"
    boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=BotoConfig(s3={"addressing_style": "path"}),
    ).create_bucket(Bucket=bucket)
    configuration = {
        "provider": "s3_self_hosted",
        "endpoint_url": endpoint,
        "bucket": bucket,
        "region": "us-east-1",
        "root": "library-root",
        "addressing_style": "path",
    }
    secrets = {"access_key": S3_ACCESS_KEY, "secret_key": S3_SECRET_KEY}
    parsed = parse_connection_config(LibrarySourceKind.S3, configuration, secrets)
    backend = OpenDALStorageBackend(resolve_transport(parsed))
    backend.ensure_setup()
    return _RemoteProvider("s3", configuration, secrets, backend)


@pytest.fixture(
    params=[
        pytest.param(
            _nextcloud_provider, id="nextcloud", marks=pytest.mark.remote_storage
        ),
        pytest.param(_sftp_provider, id="sftp", marks=pytest.mark.remote_storage),
        pytest.param(_s3_provider, id="s3", marks=pytest.mark.s3),
    ]
)
def remote_provider(request) -> _RemoteProvider:
    return request.param()


class TestRemoteLibraryScan:
    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_real_provider_scans_through_the_public_api(
        self, api, superuser_headers, e2e_db, remote_provider: _RemoteProvider
    ) -> None:
        payload = (FIXTURES_DIR / "sample.gcode").read_bytes()
        source_key = "models/critical-scan.gcode"
        remote_key = remote_provider.backend.source_key(source_key)
        await asyncio.to_thread(
            remote_provider.backend.create_bytes, payload, remote_key
        )

        enabled = await api.put(
            "/api/v1/config",
            headers=superuser_headers,
            json={"external_libraries_enabled": True},
        )
        assert enabled.status_code == 200, enabled.text
        connection = await api.post(
            "/api/v1/storage-connections",
            headers=superuser_headers,
            json={
                "name": f"critical-{remote_provider.kind}-{uuid4().hex}",
                "kind": remote_provider.kind,
                "configuration": remote_provider.configuration,
                "secrets": remote_provider.secrets,
            },
        )
        assert connection.status_code == 201, connection.text
        library = await api.post(
            "/api/v1/libraries",
            headers=superuser_headers,
            json={
                "name": f"critical {remote_provider.kind}",
                "source_kind": remote_provider.kind,
                "connection_id": connection.json()["id"],
                "source_prefix": "models",
                "scan_schedule": "",
            },
        )
        assert library.status_code == 201, library.text

        scan = await api.post(
            f"/api/v1/libraries/{library.json()['id']}/scan",
            headers=superuser_headers,
        )

        assert scan.status_code == 202, scan.text
        e2e_db.expire_all()
        file_row = e2e_db.exec(select(File).where(File.source_key == source_key)).one()
        downloaded = await api.get(
            f"/api/v1/files/{file_row.id}/download", headers=superuser_headers
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == payload
        assert (
            await asyncio.to_thread(remote_provider.backend.read_bytes, remote_key)
            == payload
        )
        info = await asyncio.to_thread(remote_provider.backend.object_info, remote_key)
        assert info is not None
        assert file_row.source_etag == info.etag
        assert file_row.source_version_id == info.version_id
        assert file_row.source_mtime == (
            info.modified_at.timestamp() if info.modified_at is not None else None
        )

    @pytest.mark.s3
    @pytest.mark.asyncio
    async def test_s3_equal_size_replacement_is_detected_through_the_production_factory(
        self, api, superuser_headers, e2e_db
    ) -> None:
        provider = _s3_provider()
        key = "models/changed.gcode"
        remote_key = provider.backend.source_key(key)
        before, after = b"G1 X1\n", b"G1 X2\n"
        await asyncio.to_thread(provider.backend.create_bytes, before, remote_key)
        enabled = await api.put(
            "/api/v1/config",
            headers=superuser_headers,
            json={"external_libraries_enabled": True},
        )
        assert enabled.status_code == 200
        connection = await api.post(
            "/api/v1/storage-connections",
            headers=superuser_headers,
            json={
                "name": "S3 changes",
                "kind": "s3",
                "configuration": provider.configuration,
                "secrets": provider.secrets,
            },
        )
        assert connection.status_code == 201, connection.text
        library = await api.post(
            "/api/v1/libraries",
            headers=superuser_headers,
            json={
                "name": "S3 changes",
                "source_kind": "s3",
                "connection_id": connection.json()["id"],
                "source_prefix": "models",
                "scan_schedule": "",
            },
        )
        assert library.status_code == 201, library.text
        url = f"/api/v1/libraries/{library.json()['id']}/scan"
        assert (await api.post(url, headers=superuser_headers)).status_code == 202
        e2e_db.expire_all()
        row = e2e_db.exec(select(File).where(File.source_key == key)).one()
        original_etag = row.source_etag
        assert row.sha256 == hashlib.sha256(before).hexdigest()
        client = boto3.client(
            "s3",
            endpoint_url=provider.configuration["endpoint_url"],
            region_name="us-east-1",
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            config=BotoConfig(s3={"addressing_style": "path"}),
        )
        await asyncio.to_thread(
            client.put_object,
            Bucket=provider.configuration["bucket"],
            Key=f"library-root/{key}",
            Body=after,
        )

        assert (await api.post(url, headers=superuser_headers)).status_code == 202

        e2e_db.refresh(row)
        assert row.sha256 == hashlib.sha256(after).hexdigest()
        assert row.source_etag != original_etag
        downloaded = await api.get(
            f"/api/v1/files/{row.id}/download", headers=superuser_headers
        )
        assert downloaded.status_code == 200
        assert downloaded.content == after
