"""Regression tests for production reverse-proxy upload contracts."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.paths import REPO_ROOT


def _root() -> Path:
    return REPO_ROOT


def test_frontend_nginx_uses_runtime_upload_limit_template() -> None:
    conf = (_root() / "frontend" / "nginx.conf").read_text()

    assert "client_max_body_size ${NGINX_CLIENT_MAX_BODY_SIZE};" in conf


def test_frontend_nginx_compresses_static_text_but_not_api_responses() -> None:
    conf = (_root() / "frontend" / "nginx.conf").read_text()

    assert "gzip on;" in conf
    assert "gzip_vary on;" in conf
    assert "application/javascript" in conf
    assert "text/css" in conf
    assert "font/woff2" not in conf
    api_location = conf.split("location /api/v1/ {", 1)[1].split("}", 1)[0]
    assert "gzip off;" in api_location


def test_frontend_image_defaults_to_backend_upload_limit() -> None:
    dockerfile = (_root() / "frontend" / "Dockerfile").read_text()

    assert "COPY nginx.conf /etc/nginx/templates/default.conf.template" in dockerfile
    assert "ENV NGINX_CLIENT_MAX_BODY_SIZE=512m" in dockerfile


def test_compose_wires_frontend_proxy_limit_from_upload_setting() -> None:
    root = REPO_ROOT
    compose = (root / "docker-compose.yml").read_text()

    assert "NGINX_CLIENT_MAX_BODY_SIZE: ${VAULT_MAX_UPLOAD_MB:-512}m" in compose
    assert "VAULT_MAX_UPLOAD_MB: ${VAULT_MAX_UPLOAD_MB:-512}" in compose


def test_default_deployments_do_not_publish_api_port() -> None:
    root = _root()
    for name in ("docker-compose.yml", "docker-compose.light.yml"):
        config = yaml.safe_load((root / name).read_text())
        api = config["services"]["api"]
        assert "ports" not in api
        assert api["expose"] == ["8000"]


def test_optional_stateful_services_do_not_publish_host_ports() -> None:
    root = _root()
    default_config = yaml.safe_load((root / "docker-compose.yml").read_text())
    for service_name in ("postgres", "seaweedfs"):
        assert "ports" not in default_config["services"][service_name]

    migration_config = yaml.safe_load(
        (root / "docker-compose.migrate-minio.yml").read_text()
    )
    assert "ports" not in migration_config["services"]["minio"]


def test_frontend_sets_browser_security_headers() -> None:
    conf = (_root() / "frontend" / "security-headers.conf").read_text()

    assert "Content-Security-Policy" in conf
    assert "frame-ancestors 'none'" in conf
    assert 'X-Content-Type-Options "nosniff"' in conf
    assert 'Referrer-Policy "strict-origin-when-cross-origin"' in conf
    assert "Permissions-Policy" in conf


def test_runtime_images_use_unprivileged_users() -> None:
    root = _root()
    backend = (root / "backend" / "Dockerfile").read_text()
    frontend = (root / "frontend" / "Dockerfile").read_text()

    assert "gosu printstash" in (root / "backend" / "docker-entrypoint.sh").read_text()
    assert "useradd" in backend
    assert "nginxinc/nginx-unprivileged:alpine" in frontend


def test_backend_uv_toolchain_image_is_immutable() -> None:
    dockerfile = (_root() / "backend" / "Dockerfile").read_text()

    assert "ghcr.io/astral-sh/uv:latest" not in dockerfile
    assert (
        "ghcr.io/astral-sh/uv:0.12.1@"
        "sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded"
        in dockerfile
    )


def test_backend_runtime_uv_fallback_uses_user_writable_cache() -> None:
    dockerfile = (_root() / "backend" / "Dockerfile").read_text()
    entrypoint = (_root() / "backend" / "docker-entrypoint.sh").read_text()

    runtime_cache = "ENV UV_CACHE_DIR=/tmp/printstash-uv-cache"
    assert runtime_cache in dockerfile
    assert "UV_NO_SYNC=1" in dockerfile
    assert dockerfile.index(runtime_cache) > dockerfile.index("useradd")
    assert 'CMD ["/app/.venv/bin/uvicorn"' in dockerfile
    assert "/app/.venv/bin/python -m app.db.migrate" in entrypoint
