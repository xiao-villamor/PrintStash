"""Storage contracts against an already built, unmodified runtime image.

Run through ``scripts/test.sh image --image TAG --variant full|lite|gpu|gpu-render``. This is
the image-build lane: unlike ASGI tests, every application dependency comes
from the final image. The host only supplies HTTP and container test clients.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
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
    openssh_endpoint,
    s3_endpoint,
    shutdown_containers,
)
from tests.paths import FIXTURES_DIR

IMAGE = ""
VARIANT = ""

TRANSPORT_PROBE = r"""
import json
from app.modules.storage.remote_io_adapters import remote_io_for
from app.modules.storage.storage_backend.contracts import StorageConfigurationError
from app.modules.storage.storage_providers import TransportKind, TransportSpec, provider_catalogue

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
        backend = remote_io_for(TransportSpec(
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


@contextmanager
def _entrypoint_layout() -> Iterator[Path]:
    """Throwaway host data shared by bind mounts, restored for host cleanup."""
    with tempfile.TemporaryDirectory(prefix="printstash-entrypoint-") as scratch:
        root = Path(scratch)
        root.chmod(0o755)
        library = root / "library"
        library.mkdir()
        (library / "part.stl").write_bytes(b"solid sample")
        try:
            yield root
        finally:
            # The entrypoint may have repaired this test's managed directories
            # to the image UID. Restore them before TemporaryDirectory removes
            # them; every byte here was created by this test.
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--mount",
                    f"type=bind,src={root},dst=/data",
                    "--entrypoint",
                    "chown",
                    IMAGE,
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    "/data",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )


def _source_metadata(
    root: Path,
) -> tuple[tuple[int, int, int], tuple[int, int, int], bytes]:
    library = root / "library"
    artifact = library / "part.stl"
    directory_stat = library.stat()
    artifact_stat = artifact.stat()
    return (
        (directory_stat.st_uid, directory_stat.st_gid, directory_stat.st_ctime_ns),
        (artifact_stat.st_uid, artifact_stat.st_gid, artifact_stat.st_ctime_ns),
        artifact.read_bytes(),
    )


class TestRuntimeImageEntrypoint(unittest.TestCase):
    """External sources are untouched; managed storage still gets repaired."""

    def _run(
        self, mounts: list[str], *, role: str = "worker"
    ) -> subprocess.CompletedProcess[str]:
        # The API case exercises real migrations. Ownership-only cases use the
        # shipped worker role to avoid creating a schema for each permission test.
        return subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--env",
                f"VAULT_PROCESS_ROLE={role}",
                "--entrypoint",
                "/app/docker-entrypoint.sh",
                *mounts,
                IMAGE,
                "/app/.venv/bin/python",
                "-c",
                "import json, os, sqlite3; from pathlib import Path; "
                "database = Path('/data/db/printstash.sqlite'); "
                "revision = sqlite3.connect(f'file:{database}?mode=ro', uri=True)"
                ".execute('SELECT version_num FROM alembic_version').fetchone()[0] "
                "if database.exists() else None; "
                "paths = ['/data', '/data/library', '/data/db', '/data/db/legacy.sqlite', "
                "'/data/artifact-cache', '/data/ai-models']; "
                "print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(), 'revision': revision, "
                "'paths': {p: [Path(p).stat().st_uid, Path(p).stat().st_gid, "
                "Path(p).stat().st_dev] for p in paths if Path(p).exists()}}))",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

    def test_starts_with_an_external_read_only_mount(self) -> None:
        with _entrypoint_layout() as root:
            result = self._run(
                [
                    "--tmpfs",
                    "/data",
                    "--mount",
                    f"type=bind,src={root / 'library'},dst=/data/library,readonly",
                ],
                role="api",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(result.stdout.splitlines()[-1])
            self.assertIsInstance(state["revision"], str)
            self.assertTrue(state["revision"])
            self.assertEqual((state["uid"], state["gid"]), (10001, 10001))
            self.assertNotEqual(
                state["paths"]["/data"][2], state["paths"]["/data/library"][2]
            )

    def test_preserves_a_same_device_read_only_bind_mount(self) -> None:
        with _entrypoint_layout() as root:
            before = _source_metadata(root)

            result = self._run(
                [
                    "--mount",
                    f"type=bind,src={root},dst=/data",
                    "--mount",
                    f"type=bind,src={root / 'library'},dst=/data/library,readonly",
                ]
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(result.stdout)
            self.assertEqual(
                state["paths"]["/data"][2], state["paths"]["/data/library"][2]
            )
            self.assertEqual(_source_metadata(root), before)

    def test_preserves_a_writable_external_bind_mount(self) -> None:
        with _entrypoint_layout() as root:
            before = _source_metadata(root)

            result = self._run(
                [
                    "--mount",
                    f"type=bind,src={root},dst=/data",
                    "--mount",
                    f"type=bind,src={root / 'library'},dst=/data/library",
                ]
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(_source_metadata(root), before)

    def test_repairs_a_separately_mounted_database_tree(self) -> None:
        with _entrypoint_layout() as root:
            database = root / "db"
            database.mkdir()
            (database / "legacy.sqlite").write_bytes(b"ownership fixture")

            result = self._run(
                [
                    "--mount",
                    f"type=bind,src={database},dst=/data/db",
                ]
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(result.stdout)
            self.assertEqual(
                state["paths"]["/data/db/legacy.sqlite"][:2], [10001, 10001]
            )
            self.assertNotEqual(
                state["paths"]["/data"][2], state["paths"]["/data/db"][2]
            )

    def test_repairs_the_cache_roots(self) -> None:
        with _entrypoint_layout() as root:
            result = self._run(["--mount", f"type=bind,src={root},dst=/data"])

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(result.stdout)
            self.assertEqual(state["paths"]["/data/artifact-cache"][:2], [10001, 10001])
            self.assertEqual(state["paths"]["/data/ai-models"][:2], [10001, 10001])

    def test_preserves_metadata_for_correctly_owned_managed_entries(self) -> None:
        with _entrypoint_layout() as root:
            files = root / "files"
            files.mkdir()
            artifact = files / "part.stl"
            artifact.write_bytes(b"owned artifact")
            mounts = ["--mount", f"type=bind,src={root},dst=/data"]
            first = self._run(mounts)
            self.assertEqual(first.returncode, 0, first.stderr)
            before = artifact.stat()

            second = self._run(mounts)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(artifact.stat(), before)

    def test_a_read_only_managed_root_stops_before_migration(self) -> None:
        with _entrypoint_layout() as root:
            result = self._run(
                [
                    "--mount",
                    f"type=bind,src={root / 'library'},dst=/data/files,readonly",
                ],
                role="api",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Read-only file system", result.stderr)
            self.assertNotIn("migrate:", result.stderr)
            self.assertEqual(result.stdout, "")


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
        try:
            self._run_image(
                {
                    "kind": "s3",
                    "configuration": {
                        "provider": "s3_self_hosted",
                        "bucket": bucket,
                        "endpoint_url": remote_endpoint,
                        "region": "us-east-1",
                        "root": "off-site",
                        "addressing_style": "path",
                    },
                    "secrets": {
                        "access_key": S3_ACCESS_KEY,
                        "secret_key": S3_SECRET_KEY,
                    },
                }
            )
        finally:
            s3.close()

    def test_restores_sftp_backup_from_shipped_image(self) -> None:
        from app.modules.storage.storage_opendal import OpenDALStorageBackend
        from app.modules.storage.storage_providers import (
            SFTPProviderConfig,
            resolve_transport,
        )

        host, port, host_key = openssh_endpoint()
        root = f"image-sftp-{uuid4().hex}"
        backend = OpenDALStorageBackend(
            resolve_transport(
                SFTPProviderConfig(
                    provider="sftp",
                    host=host,
                    port=port,
                    username="contract",
                    password="contract-only",
                    host_key=host_key,
                    root=root,
                )
            )
        )
        backend.provision_root()
        # Same server key, enrolled for the address used inside the final image.
        key_fields = host_key.split()
        image_host_key = (
            f"[host.docker.internal]:{port} {key_fields[1]} {key_fields[2]}"
        )
        self._run_image(
            {
                "kind": "sftp",
                "configuration": {
                    "host": "host.docker.internal",
                    "port": port,
                    "username": "contract",
                    "host_key": image_host_key,
                    "root": root,
                },
                "secrets": {"password": "contract-only"},
            }
        )

    def _run_image(self, profile: dict) -> None:
        container = (
            DockerContainer(IMAGE)
            .with_kwargs(extra_hosts={"host.docker.internal": "host-gateway"})
            .with_env("VAULT_SETUP_MODE", "trusted_network")
            .with_exposed_ports(8000)
            .waiting_for(
                HttpWaitStrategy(8000, "/api/v1/setup/status").with_startup_timeout(120)
            )
        )
        with container:
            base = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8000)}"
            try:
                with httpx.Client(base_url=base, timeout=180) as api:
                    self._recover(api, container, profile)
            except Exception:
                stdout, stderr = container.get_logs()
                print(stdout.decode(errors="replace"))
                print(stderr.decode(errors="replace"))
                raise

    def _completed(self, api: httpx.Client, accepted: httpx.Response) -> dict:
        """Follow the Job an accepted request queued until it settles."""
        self.assertEqual(accepted.status_code, 202, accepted.text)
        job_id = accepted.json()["job_id"]
        deadline = time.monotonic() + 120
        job: dict = {}
        while time.monotonic() < deadline:
            job = api.get(f"/api/v1/jobs/{job_id}").json()
            if job.get("state") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.2)
        self.assertEqual(job.get("state"), "completed", job)
        return job

    def _recover(
        self,
        api: httpx.Client,
        container: DockerContainer,
        profile: dict,
    ) -> None:
        api.headers["Origin"] = str(api.base_url).rstrip("/")
        preparation = api.post("/api/v1/setup/session")
        self.assertEqual(preparation.status_code, 200, preparation.text)
        api.headers["X-PrintStash-Setup-CSRF"] = preparation.json()["csrf"]
        setup = api.post(
            "/api/v1/setup",
            json={
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
                "name": "Image recovery",
                "purpose": "backup",
                **profile,
            },
        )
        self.assertEqual(connection.status_code, 201, connection.text)
        payload = (FIXTURES_DIR / "sample.gcode").read_bytes()
        uploaded = api.post(
            "/api/v1/ingest/orca",
            files={"file": ("sample.gcode", payload, "text/plain")},
            data={"model_name": "Image recovery"},
        )
        self._completed(api, uploaded)
        models = api.get("/api/v1/models").json()
        model = next(row for row in models if row["name"] == "Image recovery")
        detail = api.get(f"/api/v1/models/{model['id']}").json()
        file_id = detail["files"][0]["id"]
        meta = self._completed(api, api.post("/api/v1/backups"))["result"]
        self.assertEqual(meta["location"], f"opendal:{profile['kind']}")
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
    parser.add_argument("--variant", required=True, choices=("full", "lite", "gpu", "gpu-render"))
    arguments = parser.parse_args()
    IMAGE, VARIANT = arguments.image, arguments.variant
    classes = (
        TestRuntimeImageEntrypoint,
        *(
            (TestFullImageTransports, TestRuntimeImageBackup)
            if VARIANT in {"full", "gpu", "gpu-render"}
            else (TestLiteImageTransports,)
        ),
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
