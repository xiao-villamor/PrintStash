"""Regression tests for production reverse-proxy upload contracts."""

from __future__ import annotations

from pathlib import Path


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
