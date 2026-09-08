"""Exercise the shipped container, including real nginx and startup migrations.

Run with unittest so image CI needs only Docker and the runner's Python.
Every case owns disposable containers and a volume; no developer data is used.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import time
import unittest
import urllib.error
import urllib.request
import uuid


def docker(*args: str, timeout: int = 90) -> str:
    try:
        return subprocess.check_output(
            ["docker", *args], stderr=subprocess.STDOUT, text=True, timeout=timeout
        ).strip()
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"docker {args[0]} failed: {error.output}") from error


class TestUnifiedImage(unittest.TestCase):
    def setUp(self) -> None:
        self.image = os.environ.get("PRINTSTASH_TEST_IMAGE", "printstash:local")
        self.volume = docker("volume", "create", f"unified-test-{uuid.uuid4().hex}")
        self.addCleanup(docker, "volume", "rm", self.volume)

    def start(self, *environment: str, ready: bool = True) -> str:
        options = [item for value in environment for item in ("-e", value)]
        container = docker(
            "run",
            "-d",
            "-p",
            "127.0.0.1::3000",
            "-v",
            f"{self.volume}:/data",
            *options,
            self.image,
        )
        self.addCleanup(docker, "rm", "-f", container)
        if ready:
            try:
                port = docker("port", container, "3000/tcp").rsplit(":", 1)[1]
            except RuntimeError as error:
                self.fail(f"{error}\n{docker('logs', container)}")
            self.url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(
                        self.url + "/api/v1/health", timeout=2
                    ) as response:
                        if response.status == 200:
                            return container
                except (urllib.error.URLError, OSError, http.client.HTTPException):
                    pass
                if docker("inspect", "-f", "{{.State.Running}}", container) != "true":
                    break
                time.sleep(0.5)
            self.fail(docker("logs", container))
        return container

    def test_serves_the_application_on_one_port(self) -> None:
        self.start()

        with urllib.request.urlopen(self.url, timeout=5) as response:
            html = response.read().decode()
        with urllib.request.urlopen(self.url + "/api/v1/health", timeout=5) as response:
            health = json.load(response)

        self.assertIn('<div id="root">', html)
        self.assertEqual(health["status"], "ok")

    def test_retains_database_state_across_replacement(self) -> None:
        container = self.start()
        docker(
            "exec",
            container,
            "/app/.venv/bin/python",
            "-c",
            "import sqlite3; c=sqlite3.connect('/data/db/printstash.sqlite'); "
            "assert c.execute('SELECT version_num FROM alembic_version').fetchone(); "
            "c.execute('PRAGMA user_version=42'); c.close()",
        )
        docker("stop", "-t", "60", container)

        replacement = self.start()
        version = docker(
            "exec",
            replacement,
            "/app/.venv/bin/python",
            "-c",
            "import sqlite3; "
            "print(sqlite3.connect('/data/db/printstash.sqlite')"
            ".execute('PRAGMA user_version').fetchone()[0])",
        )

        self.assertEqual(version, "42")

    def test_runs_services_as_the_configured_identity(self) -> None:
        container = self.start("PUID=12345", "PGID=12345")

        processes = docker("top", container, "-eo", "pid,uid,gid,args").splitlines()
        services = [
            line.split(maxsplit=3)
            for line in processes
            if "nginx:" in line or "uvicorn app.main:app" in line
        ]

        self.assertGreaterEqual(len(services), 2)
        self.assertEqual({(p[1], p[2]) for p in services}, {("12345", "12345")})

    def test_serves_spa_deep_links(self) -> None:
        self.start()

        with urllib.request.urlopen(self.url + "/settings", timeout=5) as response:
            html = response.read().decode()

        self.assertIn('<div id="root">', html)

    def test_returns_404_for_missing_assets(self) -> None:
        self.start()

        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url + "/assets/missing.js", timeout=5)

        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_shuts_down_on_stop(self) -> None:
        container = self.start()

        docker("stop", "-t", "60", container)

        self.assertEqual(docker("wait", container), "0")

    def test_exits_when_a_service_exits(self) -> None:
        for service in ("api", "nginx"):
            with self.subTest(service=service):
                container = self.start()

                docker(
                    "exec",
                    "-e",
                    f"SERVICE={service}",
                    container,
                    "/app/.venv/bin/python",
                    "-c",
                    "import os, pathlib, signal; "
                    "pid = int(pathlib.Path('/tmp/printstash-nginx.pid').read_text()) "
                    "if os.environ['SERVICE'] == 'nginx' else next("
                    "int(p.name) for p in pathlib.Path('/proc').iterdir() "
                    "if p.name.isdigit() and p.joinpath('cmdline').exists() "
                    "and b'/app/.venv/bin/uvicorn' in "
                    "p.joinpath('cmdline').read_bytes().split(b'\\x00')); "
                    "os.kill(pid, signal.SIGKILL)",
                )

                self.assertNotEqual(docker("wait", container, timeout=70), "0")

    def test_rejects_invalid_startup_configuration(self) -> None:
        for setting in (
            "NGINX_CLIENT_MAX_BODY_SIZE=invalid",
            "PUID=0",
            "VAULT_DB_URL=sqlite:////proc/unwritable/printstash.sqlite",
        ):
            with self.subTest(setting=setting):
                container = self.start(setting, ready=False)

                status = docker("wait", container, timeout=60)

                self.assertNotEqual(status, "0", docker("logs", container))
