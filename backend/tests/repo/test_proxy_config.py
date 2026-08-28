"""The deployment files agreeing with each other about limits and exposure.

Nothing in this repo imports an nginx config or a compose file, so nothing else
notices when one drifts from the settings it is supposed to mirror. The failures
that follow are all of the same kind: the software is correct, the deployment is
not, and the symptom appears only on a real installation.

The upload limit is the clearest chain. A user's upload has to pass the backend's
own `body_limit`, nginx's `client_max_body_size`, and whatever the compose file
put in the frontend's environment — and the smallest of the three wins. If they
disagree, uploads fail at a size no setting in the UI explains, with a 413 from a
proxy the user does not know exists.

The rest are exposure. A default `docker compose up` must not publish the API
port or a database port on the host: a self-hoster who assumed the frontend was
the only door would have put PrintStash's database on their LAN. And both runtime
images must drop to an unprivileged user, because a container that runs as root
turns any RCE into host access. These are one-line mistakes to make in a
Dockerfile and invisible until somebody scans the host.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.paths import REPO_ROOT


def _root() -> Path:
    return REPO_ROOT


class TestFrontendNginxConf:
    def test_frontend_nginx_uses_runtime_upload_limit_template(self) -> None:
        conf = (_root() / "frontend" / "nginx.conf").read_text()

        assert "client_max_body_size ${NGINX_CLIENT_MAX_BODY_SIZE};" in conf

    def test_frontend_nginx_compresses_static_text_but_not_api_responses(self) -> None:
        conf = (_root() / "frontend" / "nginx.conf").read_text()

        assert "gzip on;" in conf
        assert "gzip_vary on;" in conf
        assert "application/javascript" in conf
        assert "text/css" in conf
        assert "font/woff2" not in conf
        api_location = conf.split("location /api/v1/ {", 1)[1].split("}", 1)[0]
        assert "gzip off;" in api_location

    def test_frontend_sets_browser_security_headers(self) -> None:
        conf = (_root() / "frontend" / "security-headers.conf").read_text()

        assert "Content-Security-Policy" in conf
        assert "frame-ancestors 'none'" in conf
        assert 'X-Content-Type-Options "nosniff"' in conf
        assert 'Referrer-Policy "strict-origin-when-cross-origin"' in conf
        assert "Permissions-Policy" in conf


def _default_request_ceiling_mb() -> int:
    """What the backend will accept for a whole request, in whole MiB.

    Derived rather than written down, so the deployment files are checked against
    the code's own arithmetic instead of a number somebody remembered.
    """
    from app.core.config import MULTIPART_OVERHEAD_BYTES, Settings

    return Settings().max_upload_mb + MULTIPART_OVERHEAD_BYTES // (1024 * 1024)


class TestFrontendDockerfile:
    def test_frontend_image_defaults_to_the_backend_request_ceiling(self) -> None:
        """nginx bounds the *request*, so its default must clear the per-file cap.

        Set to the per-file number, nginx answers a file at exactly the documented
        limit with its own HTML 413 — the request carrying that file is larger than
        the file — and the API never gets to say `upload_too_large`.
        """
        dockerfile = (_root() / "frontend" / "Dockerfile").read_text()

        assert (
            "COPY nginx.conf /etc/nginx/templates/default.conf.template" in dockerfile
        )
        assert (
            f"ENV NGINX_CLIENT_MAX_BODY_SIZE={_default_request_ceiling_mb()}m"
            in dockerfile
        )


class TestComposeFiles:
    @pytest.mark.parametrize(
        "compose_file",
        [
            "docker-compose.yml",
            "docker-compose.light.yml",
            "docker-compose.prod.yml",
            "docker-compose.manual-test.yml",
        ],
    )
    def test_every_deployment_gives_the_proxy_multipart_headroom(
        self, compose_file: str
    ) -> None:
        compose = (REPO_ROOT / compose_file).read_text()

        assert (
            f"NGINX_CLIENT_MAX_BODY_SIZE: ${{VAULT_MAX_REQUEST_MB:-{_default_request_ceiling_mb()}}}m"
            in compose.replace('"', "")
        )

    def test_compose_wires_the_backend_upload_cap_from_one_setting(self) -> None:
        compose = (REPO_ROOT / "docker-compose.yml").read_text()

        assert "VAULT_MAX_UPLOAD_MB: ${VAULT_MAX_UPLOAD_MB:-512}" in compose

    def test_the_example_env_documents_the_request_ceiling(self) -> None:
        """Two knobs with a relationship between them is only safe if it is written down."""
        example = (REPO_ROOT / ".env.example").read_text()

        assert f"# VAULT_MAX_REQUEST_MB={_default_request_ceiling_mb()}" in example

    @pytest.mark.parametrize(
        "compose_file",
        [
            "docker-compose.yml",
            "docker-compose.light.yml",
            "docker-compose.prod.yml",
            "docker-compose.manual-test.yml",
        ],
    )
    def test_every_deployment_wires_the_runtime_file_owner(self, compose_file: str) -> None:
        """The container writes the vault as a uid the host has to be able to read.

        The image drops to an unprivileged user, so every file it creates in a
        bind-mounted volume is owned by that uid. A self-hoster whose host user
        is not 10001 gets a vault they cannot read or back up from the host, and
        the only fix is a `chown -R` after the fact. `PUID`/`PGID` are how they
        say who they are, and a compose file that omits them silently takes the
        default — which is why this checks all four rather than the one a
        contributor happened to edit.
        """
        config = yaml.safe_load((_root() / compose_file).read_text())

        environment = config["services"]["api"]["environment"]
        assert environment["PUID"] == "${PUID:-10001}"
        assert environment["PGID"] == "${PGID:-10001}"

    def test_default_deployments_do_not_publish_api_port(self) -> None:
        root = _root()
        for name in ("docker-compose.yml", "docker-compose.light.yml"):
            config = yaml.safe_load((root / name).read_text())
            api = config["services"]["api"]
            assert "ports" not in api
            assert api["expose"] == ["8000"]

    def test_optional_stateful_services_do_not_publish_host_ports(self) -> None:
        root = _root()
        default_config = yaml.safe_load((root / "docker-compose.yml").read_text())
        for service_name in ("postgres", "seaweedfs"):
            assert "ports" not in default_config["services"][service_name]

        migration_config = yaml.safe_load(
            (root / "docker-compose.migrate-minio.yml").read_text()
        )
        assert "ports" not in migration_config["services"]["minio"]


class TestBackendDockerfile:
    def test_backend_uv_toolchain_image_is_immutable(self) -> None:
        dockerfile = (_root() / "backend" / "Dockerfile").read_text()

        assert "ghcr.io/astral-sh/uv:latest" not in dockerfile
        assert (
            "ghcr.io/astral-sh/uv:0.12.1@"
            "sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded"
            in dockerfile
        )

    def test_backend_runtime_uv_fallback_uses_user_writable_cache(self) -> None:
        dockerfile = (_root() / "backend" / "Dockerfile").read_text()
        entrypoint = (_root() / "backend" / "docker-entrypoint.sh").read_text()

        runtime_cache = "ENV UV_CACHE_DIR=/tmp/printstash-uv-cache"
        assert runtime_cache in dockerfile
        assert "UV_NO_SYNC=1" in dockerfile
        assert dockerfile.index(runtime_cache) > dockerfile.index("useradd")
        assert 'CMD ["/app/.venv/bin/uvicorn"' in dockerfile
        assert "/app/.venv/bin/python -m app.db.migrate" in entrypoint

    def test_runtime_images_use_unprivileged_users(self) -> None:
        """Neither runtime container may still be root when it serves traffic.

        A container running as root turns any RCE into host access. The backend
        starts as root on purpose — it has to `chown` the bind-mounted vault to
        whatever uid the operator asked for — so what matters is that it hands
        off: it re-execs itself through `gosu` under the requested identity, and
        the second pass refuses to continue if it is still not that identity.
        """
        root = _root()
        backend = (root / "backend" / "Dockerfile").read_text()
        frontend = (root / "frontend" / "Dockerfile").read_text()
        entrypoint = (root / "backend" / "docker-entrypoint.sh").read_text()

        assert 'exec gosu "$requested_identity"' in entrypoint
        assert 'requested_identity="$PUID:$PGID"' in entrypoint
        # The re-exec is only a hand-off if the second pass verifies it landed.
        assert 'if [ "$(id -u)" != "$PUID" ] || [ "$(id -g)" != "$PGID" ]; then' in entrypoint
        assert "useradd" in backend
        assert "nginxinc/nginx-unprivileged:alpine" in frontend
