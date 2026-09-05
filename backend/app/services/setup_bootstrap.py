"""Browser preparation for explicitly enabled, trusted-network first ownership.

The session prevents cross-site submissions; it is not proof of server ownership.
The deployment's access boundary decides who may claim a fresh installation.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException, Request, Response
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import DEFAULT_JWT_SECRET, settings
from app.core.time import utcnow
from app.db.models import SystemConfig, User

COOKIE = "printstash_setup"
CSRF_HEADER = "X-PrintStash-Setup-CSRF"
SESSION_SECONDS = 3600
_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def host_allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    explicit = {
        item.strip().lower().rstrip(".")
        for item in settings.setup_allowed_hosts.split(",")
        if item.strip()
    }
    if (
        host in explicit
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".home.arpa"))
    ):
        return True
    if "%" in host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(
        address.version == network.version and address in network
        for network in _NETWORKS
    )


def require_origin(request: Request) -> None:
    """Check the browser origin against the preserved Host, including its port."""
    if settings.setup_mode != "trusted_network":
        raise HTTPException(403, "setup_disabled")
    try:
        origin = urlsplit(request.headers.get("origin", ""))
        target = urlsplit(f"{request.url.scheme}://{request.headers.get('host', '')}")
        valid = (
            origin.scheme in {"http", "https"}
            and origin.scheme == target.scheme
            and origin.hostname == target.hostname
            and (origin.port or (443 if origin.scheme == "https" else 80))
            == (target.port or (443 if target.scheme == "https" else 80))
            and not origin.username
            and not origin.password
            and not origin.path
            and not origin.query
            and not origin.fragment
            and host_allowed(origin.hostname or "")
        )
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(403, "setup_origin_not_allowed")


def require_open(session: Session) -> None:
    config = session.get(SystemConfig, 1)
    if config is not None and config.configured_at is not None:
        raise HTTPException(409, "already_configured")
    if session.exec(select(User.id).limit(1)).first() is not None:
        raise HTTPException(409, "users_already_exist")


def lock_installation(session: Session) -> None:
    """Serialize first ownership in the database, not in one API process."""
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    elif connection.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(72816409531)"))
    else:
        raise HTTPException(503, "setup_database_not_supported")
    session.expire_all()
    require_open(session)


def _signing_key() -> bytes:
    secret = settings.jwt_secret.strip()
    if not secret or secret == DEFAULT_JWT_SECRET:
        raise HTTPException(503, "setup_not_ready")
    return hmac.digest(secret.encode(), b"printstash/browser-setup/v1", hashlib.sha256)


def begin(request: Request, response: Response, session: Session) -> str:
    require_origin(request)
    require_open(session)
    csrf = secrets.token_urlsafe(32)
    now = utcnow()
    ticket = jwt.encode(
        {
            "aud": "printstash-setup",
            "iat": now,
            "exp": now + timedelta(seconds=SESSION_SECONDS),
            "csrf": csrf,
        },
        _signing_key(),
        algorithm="HS256",
    )
    response.set_cookie(
        COOKIE,
        ticket,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/api/v1/setup",
    )
    response.headers["Cache-Control"] = "no-store"
    return csrf


def verify(request: Request) -> None:
    require_origin(request)
    try:
        claims = jwt.decode(
            request.cookies.get(COOKIE, ""),
            _signing_key(),
            algorithms=["HS256"],
            audience="printstash-setup",
            options={"require": ["exp", "iat", "csrf"]},
        )
        csrf = claims["csrf"]
        valid = isinstance(csrf, str) and hmac.compare_digest(
            csrf.encode(), request.headers.get(CSRF_HEADER, "").encode()
        )
    except jwt.InvalidTokenError:
        valid = False
    if not valid:
        raise HTTPException(403, "setup_session_expired")


def clear(response: Response, request: Request) -> None:
    response.delete_cookie(
        COOKIE,
        path="/api/v1/setup",
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
    )
