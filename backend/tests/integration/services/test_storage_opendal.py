from __future__ import annotations

import shutil
import socket
import subprocess
import time
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

import pytest

from app.services.storage_backend import StorageCollisionError
from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_providers import TransportKind, TransportSpec

opendal = pytest.importorskip("opendal")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture
def webdav_endpoint(tmp_path: Path):
    executable = shutil.which("wsgidav")
    if executable is None:
        pytest.skip("WsgiDAV contract dependency is not installed")
    port = _free_port()
    process = subprocess.Popen(
        [
            executable,
            "--host=127.0.0.1",
            f"--port={port}",
            f"--root={tmp_path}",
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


def _spec(endpoint: str = "memory://") -> TransportSpec:
    return TransportSpec(
        kind=TransportKind.WEBDAV,
        provider="webdav",
        namespace="webdav/vault-data",
        options={
            "endpoint_url": endpoint,
            "username": "user",
            "password": "password",
            "root": "vault-data",
        },
    )


def test_webdav_stream_create_read_and_evidence_round_trip(
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
    assert backend.capabilities.tier.value == "unguarded"
    with pytest.raises(StorageCollisionError):
        backend.create_bytes(b"replacement", key)


class _RenameFailure:
    def __init__(self) -> None:
        self.inner = opendal.Operator("memory")

    def __getattr__(self, name: str):
        return getattr(self.inner, name)

    def rename(self, source: str, destination: str) -> None:
        del source, destination
        raise OSError("rename failed")


def test_failed_remote_publication_removes_temporary_key() -> None:
    operator = _RenameFailure()
    backend = OpenDALStorageBackend(_spec(), operator=operator)
    key = backend.thumbnail_key(1)

    with pytest.raises(OSError, match="rename failed"):
        backend.create_bytes(b"thumbnail", key)

    assert not backend.exists(key)
    assert list(operator.inner.scan(".printstash-tmp")) == []


def test_remote_verified_mutations_fail_closed(webdav_endpoint: str) -> None:
    backend = OpenDALStorageBackend(_spec(webdav_endpoint))
    receipt = backend.create_bytes(b"owned", backend.thumbnail_key(2))

    assert backend.rollback_create(receipt) is False
    with pytest.raises(NotImplementedError, match="atomic_replace_not_supported"):
        backend.replace_bytes(b"new", receipt)
    assert backend.read_bytes(receipt.key) == b"owned"
