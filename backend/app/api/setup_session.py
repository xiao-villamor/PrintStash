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
from sqlmodel import Session

from app.core.config import DEFAULT_JWT_SECRET, settings
from app.core.time import utcnow
from app.modules.administration import setup_policy
from app.modules.administration.setup_bootstrap import require_open

COOKIE = "printstash_setup"
CSRF_HEADER = "X-PrintStash-Setup-CSRF"
SESSION_SECONDS = 3600
_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        # Not 100.64.0.0/10: RFC 6598 shared space numbers ISP carrier-grade NAT,
        # where other customers can reach a forwarded port. Tailscale reuses it,
        # so a tailnet address must be allowed explicitly.
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
        # Not ``.ts.net``: Tailscale Funnel publishes those names to the public
        # internet, so a tailnet name must be allowed explicitly as well.
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
    # Only a valid trusted_network policy opens the browser door; environment,
    # disabled and misconfigured settings all keep it shut.
    if not isinstance(setup_policy.current(), setup_policy.TrustedNetwork):
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


def _ticket(request: Request) -> dict:
    try:
        return jwt.decode(
            request.cookies.get(COOKIE, ""),
            _signing_key(),
            algorithms=["HS256"],
            audience="printstash-setup",
            options={"require": ["exp", "iat", "csrf"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(403, "setup_session_expired") from exc


def verify(request: Request) -> None:
    require_origin(request)
    csrf = _ticket(request)["csrf"]
    valid = isinstance(csrf, str) and hmac.compare_digest(
        csrf.encode(), request.headers.get(CSRF_HEADER, "").encode()
    )
    if not valid:
        raise HTTPException(403, "setup_session_expired")


def verify_preview(request: Request) -> None:
    """A same-site setup ticket admits the read-only example image."""
    if not isinstance(setup_policy.current(), setup_policy.TrustedNetwork):
        raise HTTPException(403, "setup_disabled")
    if not host_allowed(request.url.hostname or ""):
        raise HTTPException(403, "setup_origin_not_allowed")
    _ticket(request)


def clear(response: Response, request: Request) -> None:
    response.delete_cookie(
        COOKIE,
        path="/api/v1/setup",
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
    )
