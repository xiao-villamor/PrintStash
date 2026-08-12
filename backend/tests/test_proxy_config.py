"""Regression tests for production reverse-proxy upload contracts."""

from __future__ import annotations

from pathlib import Path

import yaml


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_frontend_nginx_uses_runtime_upload_limit_template() -> None:
    conf = (_root() / "frontend" / "nginx.conf").read_text()

    assert "client_max_body_size ${NGINX_CLIENT_MAX_BODY_SIZE};" in conf


def test_frontend_image_defaults_to_backend_upload_limit() -> None:
    dockerfile = (_root() / "frontend" / "Dockerfile").read_text()

    assert "COPY nginx.conf /etc/nginx/templates/default.conf.template" in dockerfile
    assert "ENV NGINX_CLIENT_MAX_BODY_SIZE=512m" in dockerfile


def test_compose_wires_frontend_proxy_limit_from_upload_setting() -> None:
    root = Path(__file__).resolve().parents[2]
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
