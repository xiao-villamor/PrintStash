"""The single-container deployment preserves state and supervises both services.

A dead API must not leave the SPA running indefinitely. Exercise the real shell
supervisor with stand-in executables; native CI also boots the final image.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.paths import REPO_ROOT


@pytest.fixture
def healthcheck_server():
    statuses = {"/api/v1/health": 200, "/": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(statuses[self.path])
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = (REPO_ROOT / "backend/unified/Dockerfile").read_text()
    instruction = source.split("HEALTHCHECK", 1)[1].split("\n#", 1)[0]
    command = shlex.split(instruction.split("CMD ", 1)[1])
    command = [
        sys.executable if arg == "/app/.venv/bin/python" else arg for arg in command
    ]
    command = [arg.replace("3000", str(server.server_port)) for arg in command]
    try:
        yield statuses, command
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestUnifiedHealthcheck:
    def test_reports_healthy_when_both_endpoints_respond(
        self, healthcheck_server
    ) -> None:
        _statuses, command = healthcheck_server

        result = subprocess.run(command, capture_output=True, text=True, timeout=10)

        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        ("path", "status"),
        [("/api/v1/health", 500), ("/", 503)],
        ids=["api-unavailable", "spa-unavailable"],
    )
    def test_rejects_an_unhealthy_endpoint(
        self, healthcheck_server, path, status
    ) -> None:
        statuses, command = healthcheck_server
        statuses[path] = status

        result = subprocess.run(command, capture_output=True, text=True, timeout=10)

        assert result.returncode != 0


@pytest.fixture
def supervisor(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    executable = tmp_path / "service"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """
import os, signal, sys, time
from pathlib import Path
root = Path(os.environ["HARNESS_ROOT"])
name = Path(sys.argv[0]).name
if "-t" in sys.argv:
    sys.exit(int(os.environ.get("CONFIG_EXIT", "0")))
def stop(*args):
    (root / (name + ".stopped")).touch()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
(root / (name + ".ready")).touch()
if name == os.environ.get("EXIT_SERVICE"):
    deadline = time.monotonic() + 10
    while not all((root / (n + ".ready")).exists() for n in ("nginx", "uvicorn")):
        if time.monotonic() > deadline:
            sys.exit(99)
        time.sleep(.01)
    sys.exit(int(os.environ["EXIT_CODE"]))
while True:
    signal.pause()
"""
    )
    executable.chmod(0o755)
    (tmp_path / "nginx").symlink_to(executable)
    (tmp_path / "uvicorn").symlink_to(executable)
    envsubst = tmp_path / "envsubst"
    envsubst.write_text("#!/bin/sh\ncat\n")
    envsubst.chmod(0o755)
    (tmp_path / "template").touch()
    source = (REPO_ROOT / "backend/unified/run.sh").read_text()
    source = source.replace("/app/.venv/bin/uvicorn", str(tmp_path / "uvicorn"))
    source = source.replace("/app/nginx-server.template", str(tmp_path / "template"))
    source = source.replace(
        "/tmp/printstash-nginx-server.conf", str(tmp_path / "rendered")
    )
    script = tmp_path / "run.sh"
    script.write_text(source)
    return script, {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "HARNESS_ROOT": str(tmp_path),
    }


def _await_services(directory: Path) -> None:
    deadline = time.monotonic() + 10
    while not all(
        (directory / f"{name}.ready").exists() for name in ("nginx", "uvicorn")
    ):
        if time.monotonic() > deadline:
            raise AssertionError("Supervisor did not start both services")
        time.sleep(0.01)


class TestUnifiedSupervisor:
    @pytest.mark.parametrize("service", ["nginx", "uvicorn"], ids=str)
    @pytest.mark.parametrize("code", [0, 7], ids=["restart", "failure"])
    def test_exits_when_a_child_stops(
        self, supervisor, tmp_path: Path, service: str, code: int
    ) -> None:
        script, env = supervisor
        env.update(EXIT_SERVICE=service, EXIT_CODE=str(code))

        result = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, timeout=15
        )

        assert result.returncode == code, result.stderr
        assert {p.name for p in tmp_path.glob("*.stopped")} == {
            f"{ {'nginx': 'uvicorn', 'uvicorn': 'nginx'}[service] }.stopped"
        }

    def test_shuts_down_on_sigterm(self, supervisor, tmp_path: Path) -> None:
        script, env = supervisor
        process = subprocess.Popen(
            ["bash", str(script)], env=env, start_new_session=True
        )
        try:
            _await_services(tmp_path)

            process.terminate()
            code = process.wait(timeout=10)

            assert code == 0
            assert {p.name for p in tmp_path.glob("*.stopped")} == {
                "nginx.stopped",
                "uvicorn.stopped",
            }
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()

    def test_rejects_invalid_nginx_configuration(
        self, supervisor, tmp_path: Path
    ) -> None:
        script, env = supervisor
        env["CONFIG_EXIT"] = "1"

        result = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, timeout=15
        )

        assert result.returncode == 1
        assert list(tmp_path.glob("*.ready")) == []


@pytest.fixture
def compose_config():
    def render(**overrides: str) -> dict:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(REPO_ROOT / "docker-compose.unified.yml"),
                "--env-file",
                "/dev/null",
                "config",
                "--format",
                "json",
            ],
            env={"PATH": os.environ["PATH"], **overrides},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout)

    return render


class TestUnifiedCompose:
    def test_configures_standalone_deployment(self, compose_config) -> None:
        config = compose_config()
        service = config["services"]["printstash"]

        assert set(config["services"]) == {"printstash"}
        assert service["image"] == "ghcr.io/xiao-villamor/printstash:latest"
        assert [(port["published"], port["target"]) for port in service["ports"]] == [
            ("3000", 3000)
        ]
        assert {volume["target"] for volume in service["volumes"]} == {
            "/data/files",
            "/data/thumbs",
            "/data/db",
            "/data/staging",
            "/data/backups",
        }
        assert service["restart"] == "unless-stopped"
        assert service["environment"]["VAULT_RESTART_ENABLED"] == "true"
        assert "VAULT_DB_URL" not in service["environment"]

    def test_accepts_deployment_overrides(self, compose_config) -> None:
        config = compose_config(
            PRINTSTASH_IMAGE="ghcr.io/example/printstash",
            PRINTSTASH_VERSION="1.2.3",
            PRINTSTASH_HTTP_PORT="8080",
        )
        service = config["services"]["printstash"]

        assert service["image"] == "ghcr.io/example/printstash:1.2.3"
        assert service["ports"][0]["published"] == "8080"
