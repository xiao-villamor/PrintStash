"""Real loopback contracts for the WebDAV and SFTP storage transports.

Run with ``cd backend && ./scripts/test.sh contract -q``.  These tests own the
external-process lifecycle and prove the transport behavior against actual
protocol servers rather than mocks.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import asyncssh
import opendal
import pytest

from app.services.storage_backend import (
    StorageCollisionError,
    StorageConfigurationError,
)
from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_providers import TransportKind, TransportSpec
from tests.paths import BACKEND_DIR

pytestmark = pytest.mark.contract


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _known_host_line(key: asyncssh.SSHKey, port: int) -> str:
    public = key.export_public_key("openssh")
    text = public.decode() if isinstance(public, bytes) else public
    return f"[127.0.0.1]:{port} {text.strip()}\n"


@pytest.fixture
def webdav_endpoint(tmp_path: Path):
    executable = shutil.which("wsgidav")
    if executable is None:
        venv_executable = BACKEND_DIR / ".venv" / "bin" / "wsgidav"
        if venv_executable.is_file():
            executable = str(venv_executable)
    if executable is None:
        pytest.fail(
            "WsgiDAV contract dependency is not installed; install the dev extra"
        )
    port = _free_port()
    (tmp_path / "storage").mkdir()
    config_path = tmp_path / "wsgidav.json"
    config_path.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": port,
                "provider_mapping": {"/dav/base": str(tmp_path / "storage")},
                "http_authenticator": {
                    "accept_basic": True,
                    "accept_digest": False,
                    "default_to_digest": False,
                },
                "simple_dc": {
                    "user_mapping": {
                        "*": {"webdav-user": {"password": "webdav-password"}}
                    }
                },
                "verbose": 1,
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            executable,
            f"--config={config_path}",
            "--quiet",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    endpoint = f"http://127.0.0.1:{port}/dav/base"
    try:
        for _ in range(200):
            if process.poll() is not None:
                pytest.fail("WsgiDAV contract server exited during startup")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    pass
                break
            except Exception:
                time.sleep(0.05)
        else:
            process.terminate()
            _stdout, stderr = process.communicate(timeout=5)
            pytest.fail(
                "WsgiDAV contract server did not become ready: "
                + stderr.decode(errors="replace")[-1000:]
            )
        yield endpoint
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def sftp_endpoint(tmp_path: Path):
    port = _free_port()
    private_key = tmp_path / "client-key"
    known_hosts = tmp_path / "known-hosts"
    authorized_keys = tmp_path / "authorized_keys"
    key = asyncssh.generate_private_key("ssh-ed25519")
    private_key.write_bytes(key.export_private_key("openssh"))
    private_key.chmod(0o600)
    authorized_keys.write_bytes(key.export_public_key("openssh"))
    process = subprocess.Popen(
        [
            str(BACKEND_DIR / ".venv" / "bin" / "python"),
            "-m",
            "tests.fakes.mock_sftp",
            "--port",
            str(port),
            "--root",
            str(tmp_path / "server"),
            "--authorized-keys",
            str(authorized_keys),
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
        yield port, private_key, known_hosts
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def sftp_password_endpoint(tmp_path: Path):
    port = _free_port()
    known_hosts = tmp_path / "password-known-hosts"
    process = subprocess.Popen(
        [
            str(BACKEND_DIR / ".venv" / "bin" / "python"),
            "-m",
            "tests.fakes.mock_sftp",
            "--port",
            str(port),
            "--root",
            str(tmp_path / "password-server"),
            "--password",
            "contract-secret",
            "--events",
            str(tmp_path / "sftp-events"),
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


@pytest.fixture
def changed_sftp_endpoint(tmp_path: Path):
    port = _free_port()
    private_key = tmp_path / "changed-client-key"
    known_hosts = tmp_path / "changed-known-hosts"
    authorized_keys = tmp_path / "changed-authorized-keys"
    key = asyncssh.generate_private_key("ssh-ed25519")
    private_key.write_bytes(key.export_private_key("openssh"))
    private_key.chmod(0o600)
    authorized_keys.write_bytes(key.export_public_key("openssh"))
    current: list[subprocess.Popen[str]] = []

    def start(*, record_host_key: bool) -> subprocess.Popen[str]:
        command = [
            str(BACKEND_DIR / ".venv" / "bin" / "python"),
            "-m",
            "tests.fakes.mock_sftp",
            "--port",
            str(port),
            "--root",
            str(tmp_path / "changed-server"),
            "--authorized-keys",
            str(authorized_keys),
        ]
        if record_host_key:
            command.extend(["--known-hosts", str(known_hosts)])
        process = subprocess.Popen(
            command,
            cwd=BACKEND_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        return process

    current.append(start(record_host_key=True))

    def rotate() -> None:
        current[0].terminate()
        current[0].wait(timeout=5)
        time.sleep(0.05)
        current[0] = start(record_host_key=False)

    try:
        yield port, private_key, known_hosts, rotate
    finally:
        current[0].terminate()
        current[0].wait(timeout=5)


def _spec(endpoint: str = "memory://") -> TransportSpec:
    return TransportSpec(
        kind=TransportKind.WEBDAV,
        provider="webdav",
        namespace="webdav/vault-data",
        options={
            "endpoint_url": endpoint,
            "username": "webdav-user",
            "password": "webdav-password",
            "root": "vault-data",
        },
    )


def _sftp_spec(port: int, private_key: Path, known_hosts: Path) -> TransportSpec:
    return TransportSpec(
        kind=TransportKind.SFTP,
        provider="sftp",
        namespace="sftp/vault-data",
        options={
            "host": "127.0.0.1",
            "port": port,
            "username": "printstash",
            "private_key_path": str(private_key),
            "host_key": str(known_hosts),
            "root": "vault-data",
        },
    )


class _RenameFailure:
    _printstash_test_double = True

    def __init__(self) -> None:
        self.inner = opendal.Operator("memory")

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def rename(self, source: str, destination: str) -> None:
        del source, destination
        raise OSError("rename failed")


class TestOpenDALStorageBackend:
    def test_webdav_rejects_invalid_credentials(self, webdav_endpoint: str) -> None:
        spec = _spec(webdav_endpoint)
        spec.options["password"] = "not-the-contract-password"
        backend = OpenDALStorageBackend(spec)

        with pytest.raises(opendal.exceptions.Unexpected):
            backend.ensure_setup()

    def test_webdav_stream_round_trip_preserves_evidence(
        self,
        webdav_endpoint: str,
    ) -> None:
        backend = OpenDALStorageBackend(_spec(webdav_endpoint))
        backend.ensure_setup()
        key = backend.blob_key("widget", 1, "widget.3mf")
        payload = b"remote-model" * (1024 * 1024)

        receipt = backend.create_stream(BytesIO(payload), key)

        assert backend.read_bytes(key) == payload
        assert receipt.size == len(payload)
        assert backend.object_info(key).size == len(payload)  # type: ignore[union-attr]
        assert backend.capabilities.tier.value == "guarded"
        with pytest.raises(StorageCollisionError):
            backend.create_bytes(b"replacement", key)

    def test_concurrent_webdav_create_only_allows_one_publisher(
        self, webdav_endpoint: str
    ) -> None:
        backend = OpenDALStorageBackend(_spec(webdav_endpoint))
        key = backend.blob_key("race", 1, "part.stl")

        def publish(index: int) -> tuple[int, str]:
            try:
                backend.create_bytes(f"publisher-{index}".encode(), key)
            except StorageCollisionError:
                return index, "collision"
            return index, "created"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(publish, range(2)))
        winners = [index for index, outcome in outcomes if outcome == "created"]

        assert [outcome for _index, outcome in outcomes].count("collision") == 1
        assert len(winners) == 1
        assert backend.read_bytes(key) == f"publisher-{winners[0]}".encode()

    def test_sftp_mounted_key_stream_round_trip(self, sftp_endpoint) -> None:
        port, private_key, known_hosts = sftp_endpoint
        backend = OpenDALStorageBackend(_sftp_spec(port, private_key, known_hosts))
        backend.provision_root()
        backend.ensure_setup()
        key = backend.blob_key("sftp-widget", 1, "widget.3mf")
        payload = b"sftp-model" * (1024 * 1024)

        receipt = backend.create_stream(BytesIO(payload), key)

        assert b"".join(backend.stream_chunks(key, 64 * 1024)) == payload
        assert receipt.size == len(payload)
        assert backend.object_info(key).size == len(payload)  # type: ignore[union-attr]
        assert backend.capabilities.tier.value == "guarded"

    def test_sftp_requires_explicit_provision_before_first_setup(
        self, sftp_endpoint
    ) -> None:
        port, private_key, known_hosts = sftp_endpoint
        backend = OpenDALStorageBackend(_sftp_spec(port, private_key, known_hosts))

        with pytest.raises(asyncssh.SFTPNoSuchFile):
            backend.ensure_setup()

    def test_sftp_health_fails_closed_after_an_enrolled_root_disappears(
        self, sftp_endpoint, tmp_path
    ) -> None:
        port, private_key, known_hosts = sftp_endpoint
        backend = OpenDALStorageBackend(_sftp_spec(port, private_key, known_hosts))
        backend.provision_root()
        backend.ensure_setup()

        shutil.rmtree(tmp_path / "server" / "vault-data")

        assert backend.health_probe()["ok"] is False
        with pytest.raises(asyncssh.SFTPNoSuchFile):
            backend.create_bytes(
                b"must not recreate", backend.blob_key("lost", 1, "x.stl")
            )
        assert not (tmp_path / "server" / "vault-data").exists()

    def test_sftp_password_stream_round_trip(self, sftp_password_endpoint) -> None:
        port, known_hosts = sftp_password_endpoint
        spec = TransportSpec(
            kind=TransportKind.SFTP,
            provider="sftp",
            namespace="sftp/vault-data",
            options={
                "host": "127.0.0.1",
                "port": port,
                "username": "printstash",
                "password": "contract-secret",
                "host_key": str(known_hosts),
                "root": "vault-data",
            },
        )
        backend = OpenDALStorageBackend(spec)
        backend.provision_root()
        backend.ensure_setup()
        key = backend.blob_key("password-widget", 1, "widget.3mf")
        payload = b"password-sftp-model" * (1024 * 1024)

        receipt = backend.create_stream(BytesIO(payload), key)

        assert b"".join(backend.stream_chunks(key, 64 * 1024)) == payload
        assert receipt.size == len(payload)

    def test_sftp_closes_an_abandoned_stream(
        self,
        sftp_password_endpoint,
        tmp_path: Path,
    ) -> None:
        port, known_hosts = sftp_password_endpoint
        source = tmp_path / "password-server" / "vault-data" / "stream.gcode"
        source.parent.mkdir()
        source.write_bytes(b"streamed" * 1024)
        backend = OpenDALStorageBackend(
            TransportSpec(
                kind=TransportKind.SFTP,
                provider="sftp",
                namespace="vault-data",
                options={
                    "host": "127.0.0.1",
                    "port": port,
                    "username": "printstash",
                    "password": "contract-secret",
                    "host_key": str(known_hosts),
                    "root": "vault-data",
                },
            )
        )
        stream = backend.stream_chunks("vault-data/stream.gcode", 8)

        assert next(stream) == b"streamed"
        stream.close()

        events_path = tmp_path / "sftp-events"
        deadline = time.monotonic() + 5
        events = events_path.read_text().splitlines()
        while events.count("connected") != events.count("disconnected"):
            assert time.monotonic() < deadline, events
            time.sleep(0.01)
            events = events_path.read_text().splitlines()
        assert events == ["connected", "disconnected"]

    def test_sftp_accepts_a_pinned_known_host_entry(self, sftp_endpoint) -> None:
        port, private_key, known_hosts = sftp_endpoint
        spec = _sftp_spec(port, private_key, known_hosts)
        spec.options["host_key"] = known_hosts.read_text(encoding="utf-8")
        backend = OpenDALStorageBackend(spec)
        backend.provision_root()

        backend.ensure_setup()

        assert backend.health_probe()["ok"] is True

    def test_concurrent_sftp_create_only_allows_one_publisher(
        self, sftp_endpoint
    ) -> None:
        port, private_key, known_hosts = sftp_endpoint
        backend = OpenDALStorageBackend(_sftp_spec(port, private_key, known_hosts))
        backend.provision_root()
        key = backend.blob_key("race", 1, "part.stl")

        def publish(index: int) -> tuple[int, str]:
            try:
                backend.create_bytes(f"publisher-{index}".encode(), key)
            except StorageCollisionError:
                return index, "collision"
            return index, "created"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(publish, range(2)))
        winners = [index for index, outcome in outcomes if outcome == "created"]

        assert [outcome for _index, outcome in outcomes].count("collision") == 1
        assert len(winners) == 1
        assert backend.read_bytes(key) == f"publisher-{winners[0]}".encode()

    def test_sftp_rejects_a_missing_host_key(self, sftp_endpoint) -> None:
        port, private_key, _known_hosts = sftp_endpoint
        spec = _sftp_spec(port, private_key, Path(""))
        spec.options["host_key"] = ""

        with pytest.raises(StorageConfigurationError, match="sftp_host_key_required"):
            OpenDALStorageBackend(spec)

    def test_sftp_rejects_a_wrong_host_key(self, sftp_endpoint, tmp_path: Path) -> None:
        port, private_key, _known_hosts = sftp_endpoint
        wrong_hosts = tmp_path / "wrong-known-hosts"
        wrong_key = asyncssh.generate_private_key("ssh-ed25519")
        wrong_hosts.write_text(_known_host_line(wrong_key, port), encoding="utf-8")
        backend = OpenDALStorageBackend(_sftp_spec(port, private_key, wrong_hosts))

        with pytest.raises(asyncssh.HostKeyNotVerifiable):
            backend.ensure_setup()

    def test_sftp_rejects_a_changed_server_host_key(
        self, changed_sftp_endpoint
    ) -> None:
        port, private_key, known_hosts, rotate = changed_sftp_endpoint
        backend = OpenDALStorageBackend(_sftp_spec(port, private_key, known_hosts))
        backend.provision_root()
        backend.ensure_setup()
        rotate()
        changed_backend = OpenDALStorageBackend(
            _sftp_spec(port, private_key, known_hosts)
        )

        with pytest.raises(asyncssh.HostKeyNotVerifiable):
            changed_backend.ensure_setup()

    def test_webdav_guarded_cleanup_retains_matching_bytes(
        self, webdav_endpoint: str
    ) -> None:
        backend = OpenDALStorageBackend(_spec(webdav_endpoint))
        key = backend.thumbnail_key(31)
        payload = b"guarded-bytes"
        backend.ensure_setup()
        backend.create_bytes(payload, key)

        assert (
            backend.reclaim_unverified(
                key,
                expected_size=len(payload),
                expected_etag=None,
            )
            is False
        )
        assert backend.read_bytes(key) == payload

    def test_failed_remote_publication_removes_temporary_key(self) -> None:
        operator = _RenameFailure()
        backend = OpenDALStorageBackend(_spec(), operator=operator)
        key = backend.thumbnail_key(1)

        with pytest.raises(OSError, match="rename failed"):
            backend.create_bytes(b"thumbnail", key)

        assert not backend.exists(key)
        assert list(operator.inner.scan(".printstash-tmp")) == []

    def test_remote_verified_mutations_fail_closed(self, webdav_endpoint: str) -> None:
        backend = OpenDALStorageBackend(_spec(webdav_endpoint))
        receipt = backend.create_bytes(b"owned", backend.thumbnail_key(2))

        assert backend.rollback_create(receipt) is False
        with pytest.raises(NotImplementedError, match="atomic_replace_not_supported"):
            backend.replace_bytes(b"new", receipt)
        assert backend.read_bytes(receipt.key) == b"owned"
