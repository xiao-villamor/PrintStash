"""A tier guard must not survive a test that overrides its socket boundaries.

The application fixture owns monkeypatch before the directory-level guard. A
manual guard restore followed by fixture undo used to reinstall the dead guard
in a later E2E test. This subprocess reproduces that exact fixture order without
opening a network connection or loading the application.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.paths import REPO_ROOT


class TestNetworkGuardLifecycle:
    @pytest.mark.parametrize("channel", ("dns", "connect", "connect-ex"), ids=str)
    @pytest.mark.parametrize("resource", ("unmarked", "s3", "postgres"), ids=str)
    def test_restores_the_original_boundary_after_a_test_override(
        self, tmp_path: Path, channel: str, resource: str
    ) -> None:
        (tmp_path / "pytest.ini").write_text(
            "[pytest]\naddopts =\nmarkers =\n    s3: owned resource\n    postgres: owned resource\n"
        )
        (tmp_path / "conftest.py").write_text(
            "import socket\nimport pytest\n"
            "def pytest_configure():\n"
            "    socket.getaddrinfo = lambda *args, **kwargs: 'original'\n"
            "    socket.socket.connect = lambda *args, **kwargs: 'original'\n"
            "    socket.socket.connect_ex = lambda *args, **kwargs: 'original'\n"
            "@pytest.fixture(autouse=True)\n"
            "def fixture_owner(monkeypatch):\n"
            "    yield\n"
        )
        protected = tmp_path / "unit"
        following = tmp_path / "e2e"
        protected.mkdir()
        following.mkdir()
        (protected / "conftest.py").write_text(
            "from tests._guards import block_real_network\n"
        )
        operations = {
            "dns": ("socket", "getaddrinfo", "'public.example', 80"),
            "connect": ("socket.socket", "connect", "peer, ('93.184.216.34', 80)"),
            "connect-ex": (
                "socket.socket",
                "connect_ex",
                "peer, ('93.184.216.34', 80)",
            ),
        }
        target, attribute, arguments = operations[channel]
        operation = f"{target}.{attribute}({arguments})"
        observed = (
            f"    with pytest.raises(RealNetworkAccess):\n        {operation}\n"
            if resource == "unmarked"
            else f"    assert {operation} == 'original'\n"
        )
        marker = "" if resource == "unmarked" else f"@pytest.mark.{resource}\n"
        (protected / "test_override.py").write_text(
            "import socket\nimport pytest\n"
            "from tests._guards import RealNetworkAccess\n"
            + marker
            + "def test_override(monkeypatch):\n"
            "    peer = socket.socket()\n"
            + observed
            + f"    monkeypatch.setattr({target}, '{attribute}', lambda *args, **kwargs: 'temporary')\n"
            + f"    assert {operation} == 'temporary'\n"
            "    peer.close()\n"
        )
        (following / "test_following.py").write_text(
            "import socket\n"
            "def test_following():\n"
            "    with socket.socket() as peer:\n"
            + f"        assert {operation} == 'original'\n"
        )

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "unit", "e2e"],
            cwd=tmp_path,
            env={
                **os.environ,
                "PYTHONPATH": str(REPO_ROOT / "backend"),
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTEST_ADDOPTS": "",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "2 passed" in result.stdout
