"""Real protocol server fixtures shared by storage contracts."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from tests.paths import BACKEND_DIR


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


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


@pytest.fixture(params=["nextcloud", "wsgidav"])
def cleanup_endpoint(request, webdav_endpoint, tmp_path):
    """A disposable prefix on each pinned evidence server."""
    import uuid
    from importlib.metadata import version

    import httpx

    from tests.containers import nextcloud_endpoint

    if request.param == "nextcloud":
        base = nextcloud_endpoint() + "/remote.php/dav/files/admin"
        auth = ("admin", "contract-only")
    else:
        assert version("wsgidav") == "4.3.5"
        base = webdav_endpoint
        auth = ("webdav-user", "webdav-password")
    folder = "cleanup-" + uuid.uuid4().hex
    root = base + "/" + folder

    def set_mtime(name: str, timestamp: int):
        # Hold the real filesystem observation at a chosen second, making the
        # rapid-write boundary reproducible even on a heavily loaded runner.
        if request.param == "wsgidav":
            import os

            os.utime(tmp_path / "storage" / folder / name, (timestamp, timestamp))

    with httpx.Client(auth=auth, timeout=30) as client:
        assert client.request("MKCOL", root).status_code == 201
        try:
            yield request.param, root, auth, set_mtime
        finally:
            # Only this fixture's disposable random prefix is removed.
            client.delete(root)
