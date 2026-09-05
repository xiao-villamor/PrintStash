"""Storage contracts against an already built, unmodified runtime image.

Run through ``scripts/test.sh image --image TAG --variant full|lite``. This is
the image-build lane: unlike ASGI tests, every application dependency comes
from the final image. The host only supplies HTTP and container test clients.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import unittest
from urllib.parse import urlsplit
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from tests.containers import (
    S3_ACCESS_KEY,
    S3_SECRET_KEY,
    s3_endpoint,
    shutdown_containers,
)
from tests.paths import FIXTURES_DIR

IMAGE = ""
VARIANT = ""
SETUP_TOKEN = "image-contract-only-setup-token"

TRANSPORT_PROBE = r"""
import json
from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_backend import StorageConfigurationError
from app.services.storage_providers import TransportKind, TransportSpec, provider_catalogue

options = {
    "root": "image-contract", "bucket": "image-contract", "region": "us-east-1",
    "access_key": "contract-only", "secret_key": "contract-only",
    "endpoint_url": "https://example.invalid", "username": "contract-only",
    "password": "contract-only", "host": "example.invalid", "port": 22,
    "host_key": "contract-only", "client_id": "contract-only",
    "client_secret": "contract-only", "refresh_token": "contract-only",
}
results = {}
for kind in TransportKind:
    if kind is TransportKind.LOCAL:
        continue
    try:
        backend = OpenDALStorageBackend(TransportSpec(
            kind=kind, provider=kind.value, namespace="image-contract", options=options,
        ))
    except StorageConfigurationError as exc:
        results[kind.value] = {"available": False, "reason": str(exc)}
    else:
        results[kind.value] = {"available": True, "namespace": backend.source_namespace}
print(json.dumps({
    "transports": results,
    "providers": {p.id: p.available for p in provider_catalogue()},
}))
"""


def _probe() -> dict:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--entrypoint",
            "/app/.venv/bin/python",
            IMAGE,
            "-",
        ],
        input=TRANSPORT_PROBE,
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    return json.loads(result.stdout)


class TestFullImageTransports(unittest.TestCase):
    def test_constructs_advertised_image_transports(self) -> None:
        result = _probe()

        self.assertEqual(
            result["transports"],
            {
                kind: {"available": True, "namespace": "image-contract"}
                for kind in ("s3", "webdav", "sftp", "gdrive")
            },
        )


class TestLiteImageTransports(unittest.TestCase):
    def test_reports_unavailable_image_capabilities(self) -> None:
        result = _probe()

        self.assertEqual(
            result["transports"],
            {
                kind: {"available": False, "reason": "Requires the full image"}
                for kind in ("s3", "webdav", "sftp", "gdrive")
            },
        )
        self.assertTrue(result["providers"]["local"])
        self.assertTrue(result["providers"]["s3"])
        self.assertFalse(result["providers"]["webdav"])
        self.assertFalse(result["providers"]["sftp"])
        self.assertFalse(result["providers"]["gdrive"])


class TestRuntimeImageBackup(unittest.TestCase):
    def test_restores_s3_backup_from_shipped_image(self) -> None:
        endpoint = s3_endpoint()
        bucket = f"image-recovery-{uuid4().hex[:12]}"
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            config=Config(s3={"addressing_style": "path"}),
        )
        s3.create_bucket(Bucket=bucket)
        # Linux native runners expose the suite-owned service through Docker's
        # host gateway; no provider image/version is defined a second time here.
        remote_endpoint = f"http://host.docker.internal:{urlsplit(endpoint).port}"
        container = (
            DockerContainer(IMAGE)
            .with_kwargs(extra_hosts={"host.docker.internal": "host-gateway"})
            .with_env("VAULT_SETUP_TOKEN", SETUP_TOKEN)
            .with_exposed_ports(8000)
            .waiting_for(
                HttpWaitStrategy(8000, "/api/v1/setup").with_startup_timeout(120)
            )
        )
        try:
            with container:
                base = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8000)}"
                try:
                    with httpx.Client(base_url=base, timeout=180) as api:
                        self._recover(api, container, bucket, remote_endpoint)
                except Exception:
                    stdout, stderr = container.get_logs()
                    print(stdout.decode(errors="replace"))
                    print(stderr.decode(errors="replace"))
                    raise
        finally:
            s3.close()

    def _recover(
        self,
        api: httpx.Client,
        container: DockerContainer,
        bucket: str,
        endpoint: str,
    ) -> None:
        setup = api.post(
            "/api/v1/setup",
            json={
                "setup_token": SETUP_TOKEN,
                "username": "image-owner",
                "password": "ImageContractPassword123",
                "storage_backend": "local",
                "data_dir": "/data/files",
                "thumb_dir": "/data/thumbs",
            },
        )
        self.assertEqual(setup.status_code, 201, setup.text)
        api.headers["Authorization"] = f"Bearer {setup.json()['access_token']}"
        configured = api.put(
            "/api/v1/config", json={"manual_local_backup_enabled": False}
        )
        self.assertEqual(configured.status_code, 200, configured.text)
        connection = api.post(
            "/api/v1/storage-connections",
            json={
                "name": "Image S3 recovery",
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
                "secrets": {"access_key": S3_ACCESS_KEY, "secret_key": S3_SECRET_KEY},
            },
        )
        self.assertEqual(connection.status_code, 201, connection.text)
        payload = (FIXTURES_DIR / "sample.gcode").read_bytes()
        uploaded = api.post(
            "/api/v1/ingest/orca",
            files={"file": ("sample.gcode", payload, "text/plain")},
            data={"model_name": "Image recovery"},
        )
        self.assertEqual(uploaded.status_code, 202, uploaded.text)
        deadline = time.monotonic() + 120
        job = {}
        while time.monotonic() < deadline:
            job = api.get(f"/api/v1/ingest/jobs/{uploaded.json()['job_id']}").json()
            if job["state"] in {"completed", "failed", "duplicate"}:
                break
            time.sleep(0.1)
        self.assertEqual(job.get("state"), "completed", job)
        models = api.get("/api/v1/models").json()
        model = next(row for row in models if row["name"] == "Image recovery")
        detail = api.get(f"/api/v1/models/{model['id']}").json()
        file_id = detail["files"][0]["id"]
        created = api.post("/api/v1/backups")
        self.assertEqual(created.status_code, 202, created.text)
        meta = created.json()
        self.assertEqual(meta["location"], "opendal:s3")
        listed = api.get("/api/v1/backups/sources").json()
        self.assertIn(meta["source_ref"], [row["source_ref"] for row in listed])
        removed = api.delete(f"/api/v1/models/{model['id']}")
        self.assertEqual(removed.status_code, 204, removed.text)
        purged = api.delete(f"/api/v1/models/{model['id']}/purge")
        self.assertEqual(purged.status_code, 200, purged.text)
        self.assertEqual(api.get(f"/api/v1/files/{file_id}/download").status_code, 404)
        restored = api.post(
            f"/api/v1/backups/{meta['backup_id']}/restore",
            params={"source_ref": meta["source_ref"]},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        downloaded = api.get(f"/api/v1/files/{file_id}/download")
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.content, payload)
        no_local_archive = container.exec(
            [
                "/app/.venv/bin/python",
                "-c",
                "from pathlib import Path; assert not list(Path('/data/backups').glob('*.tar.gz'))",
            ]
        )
        self.assertEqual(no_local_archive.exit_code, 0, no_local_archive.output)


def main() -> int:
    global IMAGE, VARIANT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--variant", required=True, choices=("full", "lite"))
    arguments = parser.parse_args()
    IMAGE, VARIANT = arguments.image, arguments.variant
    classes = (
        (TestFullImageTransports, TestRuntimeImageBackup)
        if VARIANT == "full"
        else (TestLiteImageTransports,)
    )
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromTestCase(case) for case in classes
    )
    try:
        return (
            0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
        )
    finally:
        shutdown_containers()


if __name__ == "__main__":
    raise SystemExit(main())
