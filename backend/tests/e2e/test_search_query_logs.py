"""The shipped proxy must not log submitted queries, including upstream failure."""

import hashlib
import os
import socket
import subprocess
import time

import httpx
import pytest

from tests import containers
from tests.paths import REPO_ROOT

pytestmark = pytest.mark.slow


class TestSearchQueryLogs:
    def test_omits_queries_when_upstream_is_unavailable(self, tmp_path):
        containers.require_docker("nginx search logging boundary")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = (
            (REPO_ROOT / "frontend/nginx.conf")
            .read_text()
            .replace("${NGINX_CLIENT_MAX_BODY_SIZE}", "528m")
            .replace("listen 3000;", f"listen {port};")
            .replace("http://api:8000", "http://127.0.0.1:1")
        )
        config = tmp_path / "nginx.conf"
        config.write_text(
            "pid /tmp/nginx.pid; error_log stderr; events {} http { access_log /dev/stdout; "
            + server
            + " }"
        )
        config.chmod(0o644)
        name = (
            "printstash-query-logs-"
            + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
        )
        started = False
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--network",
                    "host",
                    "--mount",
                    f"type=bind,source={config},target=/etc/nginx/nginx.conf,readonly",
                    "--mount",
                    f"type=bind,source={REPO_ROOT / 'frontend/security-headers.conf'},target=/etc/nginx/security-headers.conf,readonly",
                    "--entrypoint",
                    "nginx",
                    os.environ.get(
                        "NGINX_TEST_IMAGE", "nginxinc/nginx-unprivileged:alpine"
                    ),
                    "-g",
                    "daemon off;",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            started = True
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False
            ) as client:
                for _ in range(100):
                    try:
                        client.get("/api/v1/health")
                        break
                    except httpx.TransportError:
                        time.sleep(0.05)
                response = client.get("/api/v1/search?q=private-proxy-query-marker")
                assert response.status_code == 502
                response = client.post(
                    "/api/v1/search/parse",
                    json={"query": "private-proxy-prompt-marker"},
                )
                assert response.status_code == 502
                client.get("/search?q=private-spa-query-marker")
            logs = subprocess.run(
                ["docker", "logs", name],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
            output = logs.stdout + logs.stderr
            assert "private-proxy-query-marker" not in output
            assert "private-proxy-prompt-marker" not in output
            assert "private-spa-query-marker" not in output
        finally:
            if started:
                subprocess.run(
                    ["docker", "rm", "--force", name], capture_output=True, timeout=30
                )
