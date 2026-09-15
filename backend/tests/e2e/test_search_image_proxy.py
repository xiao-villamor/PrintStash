"""The shipped nginx route streams image bodies into the real in-memory API.

A read-only request-body directory makes even transient nginx spooling fail.
NGINX_TEST_IMAGE may select an already-built frontend image; CI uses the same
nginx base image as frontend/Dockerfile.
"""

import hashlib
import io
import os
import socket
import subprocess
import time

import httpx
import pytest
from PIL import Image
from sqlmodel import select

from app.db.models import PassageVector
from app.db.session import get_session_factory, override_session_factory
from app.modules.search import configuration
from app.schemas.inference import SearchSettings
from tests import containers
from tests.factories import bearer, build_user
from tests.fakes.server import start_server
from tests.paths import REPO_ROOT

pytestmark = pytest.mark.slow


class TestSearchImageProxy:
    def test_streams_image_requests_without_disk(
        self, app, db_session, tmp_path, monkeypatch
    ):
        containers.require_docker("nginx image-query boundary")
        actor = build_user(db_session, superuser=True)
        configuration.update(db_session, SearchSettings(enabled=True))
        db_session.commit()
        factory = get_session_factory()

        async def configured_app(scope, receive, send):
            override_session_factory(factory)
            await app(scope, receive, send)

        upstream = start_server(configured_app)
        with socket.socket() as socket_:
            socket_.bind(("127.0.0.1", 0))
            port = socket_.getsockname()[1]
        upstream_port = upstream.base_url.rsplit(":", 1)[1]
        server = (
            (REPO_ROOT / "frontend/nginx.conf")
            .read_text()
            .replace("${NGINX_CLIENT_MAX_BODY_SIZE}", "528m")
            .replace("listen 3000;", f"listen {port};")
            .replace("http://api:8000", f"http://127.0.0.1:{upstream_port}")
        )
        configuration_file = tmp_path / "nginx.conf"
        configuration_file.write_text(
            "pid /tmp/nginx.pid; error_log stderr; events {} http { "
            "client_body_temp_path /query-body; proxy_temp_path /tmp/proxy; "
            "fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi; "
            "access_log off; " + server + " }"
        )
        configuration_file.chmod(0o644)
        body_directory = tmp_path / "body"
        body_directory.mkdir(mode=0o755)
        name = (
            "printstash-image-proxy-"
            + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
        )
        image = os.environ.get("NGINX_TEST_IMAGE", "nginxinc/nginx-unprivileged:alpine")
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
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=16m",
                    "--mount",
                    f"type=bind,source={configuration_file},target=/etc/nginx/nginx.conf,readonly",
                    "--mount",
                    f"type=bind,source={body_directory},target=/query-body,readonly",
                    "--mount",
                    f"type=bind,source={REPO_ROOT / 'frontend/security-headers.conf'},target=/etc/nginx/security-headers.conf,readonly",
                    "--entrypoint",
                    "nginx",
                    image,
                    "-g",
                    "daemon off;",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            started = True
            origin = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=origin, timeout=20, trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise AssertionError(
                        subprocess.run(
                            ["docker", "logs", name], capture_output=True, text=True
                        ).stderr
                    )
                # Incompressible, valid 3-MiB image exceeds nginx's ordinary body buffer.
                import random

                encoded = io.BytesIO()
                Image.frombytes(
                    "RGB", (1024, 1024), random.Random(166).randbytes(3 * 1024**2)
                ).save(encoded, "PNG")
                raw = encoded.getvalue()
                assert len(raw) > 3_000_000
                import tempfile

                def no_spool(*args, **kwargs):
                    raise AssertionError("API query payload reached disk")

                for method in (
                    "TemporaryFile",
                    "SpooledTemporaryFile",
                    "NamedTemporaryFile",
                    "mkstemp",
                ):
                    monkeypatch.setattr(tempfile, method, no_spool)
                before = db_session.exec(select(PassageVector.id)).all()
                for multipart in (False, True):
                    content_type = "image/png"
                    content = raw
                    if multipart:
                        content_type = "multipart/form-data; boundary=search-memory"
                        content = (
                            b'--search-memory\r\nContent-Disposition: form-data; name="image"; filename="private.png"\r\nContent-Type: image/png\r\n\r\n'
                            + raw
                            + b"\r\n--search-memory--\r\n"
                        )
                    chunks = (
                        content[index : index + 32768]
                        for index in range(0, len(content), 32768)
                    )
                    response = client.post(
                        "/api/v1/search/image",
                        content=chunks,
                        headers={**bearer(actor), "Content-Type": content_type},
                    )
                    assert response.status_code == 200, response.text
                    assert response.headers["cache-control"] == "no-store"
                    assert response.json()["degraded"] == ["search_visual_unavailable"]
                assert list(body_directory.iterdir()) == []
                assert db_session.exec(select(PassageVector.id)).all() == before
        finally:
            if started:
                subprocess.run(
                    ["docker", "rm", "--force", name], capture_output=True, timeout=20
                )
            upstream.stop()
