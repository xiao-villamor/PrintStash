"""SSRF-safe URL normalization, resolution, and pinned HTTP transports.

Callers resolve a user-controlled URL once and then connect through a transport
that dials the validated address. The original hostname remains in the URL so
HTTP Host, TLS SNI, and certificate verification keep their normal semantics.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Iterable, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from httpcore import AnyIOBackend, AsyncNetworkStream, NetworkStream, SyncBackend


class UnsafeUrlError(Exception):
    """The URL is unsafe for a server-side fetch; ``reason`` is stable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def normalize_http_url(value: str, *, strip_credentials: bool = False) -> str:
    """Return a canonical HTTP(S) URL without performing a DNS lookup."""
    stripped = value.strip()
    if not stripped or any(ord(char) < 32 or ord(char) == 127 for char in stripped):
        raise UnsafeUrlError("url_invalid")
    try:
        parts = urlsplit(stripped)
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError("url_invalid") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise UnsafeUrlError("url_scheme_not_allowed")
    if not parts.hostname:
        raise UnsafeUrlError("url_host_missing")
    if not strip_credentials and (
        parts.username is not None or parts.password is not None
    ):
        raise UnsafeUrlError("url_credentials_not_allowed")

    try:
        hostname = parts.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise UnsafeUrlError("url_invalid") from exc
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit(
        (parts.scheme.lower(), netloc, parts.path or "/", parts.query, parts.fragment)
    )


def is_public_ip(ip_str: str) -> bool:
    """Return whether Python classifies an address as globally routable."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return ip.is_global


@dataclass(frozen=True)
class PinnedTarget:
    """Normalized URL and the validated address that its transport must dial."""

    url: str
    host: str
    port: int
    ip: str


def resolve_public_target(
    url: str, *, ip_validator: Callable[[str], bool] = is_public_ip
) -> PinnedTarget:
    """Resolve once, reject any non-public answer, and pin the first address."""
    normalized = normalize_http_url(url)
    parts = urlsplit(normalized)
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("url_host_missing")
    port = parts.port or (443 if parts.scheme == "https" else 80)

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError("url_dns_resolution_failed") from exc

    addrs = [cast(str, info[4][0]) for info in infos]
    if not addrs:
        raise UnsafeUrlError("url_dns_resolution_failed")
    if any(not ip_validator(addr) for addr in addrs):
        raise UnsafeUrlError("url_target_not_public")

    return PinnedTarget(url=normalized, host=host, port=port, ip=addrs[0])


def is_public_url(
    url: str, *, ip_validator: Callable[[str], bool] = is_public_ip
) -> bool:
    """Boolean form of :func:`resolve_public_target`."""
    try:
        resolve_public_target(url, ip_validator=ip_validator)
    except UnsafeUrlError:
        return False
    return True


class _PinnedBackend(AnyIOBackend):
    def __init__(self, ip: str) -> None:
        self._ip = ip

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[object] | None = None,
    ) -> AsyncNetworkStream:
        return await super().connect_tcp(
            self._ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,  # type: ignore[arg-type]
        )


class _PinnedSyncBackend(SyncBackend):
    def __init__(self, ip: str) -> None:
        self._ip = ip

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[object] | None = None,
    ) -> NetworkStream:
        return super().connect_tcp(
            self._ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,  # type: ignore[arg-type]
        )


def pinned_transport(target: PinnedTarget) -> httpx.AsyncHTTPTransport:
    """Build an async transport pinned to a previously validated address."""
    transport = httpx.AsyncHTTPTransport(retries=0)
    transport._pool._network_backend = _PinnedBackend(target.ip)  # type: ignore[attr-defined]
    return transport


def pinned_sync_transport(target: PinnedTarget) -> httpx.HTTPTransport:
    """Build a synchronous transport pinned to a validated address."""
    transport = httpx.HTTPTransport(retries=0)
    transport._pool._network_backend = _PinnedSyncBackend(target.ip)  # type: ignore[attr-defined]
    return transport
