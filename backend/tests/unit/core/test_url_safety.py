"""Unit coverage for the shared SSRF guard (``app.core.url_safety``)."""

from __future__ import annotations

import socket

import httpx
import pytest
from httpcore import AnyIOBackend, SyncBackend

from app.core.url_safety import (
    UnsafeUrlError,
    is_public_ip,
    is_public_url,
    normalize_http_url,
    pinned_sync_transport,
    pinned_transport,
    resolve_public_target,
)


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("127.0.0.1", False),  # loopback
        ("10.0.0.1", False),  # private
        ("192.168.1.5", False),  # private
        ("169.254.169.254", False),  # link-local (cloud metadata)
        ("100.64.0.1", False),  # carrier-grade NAT
        ("198.18.0.1", False),  # benchmark range
        ("192.0.2.1", False),  # documentation range
        ("::1", False),  # ipv6 loopback
        ("fc00::1", False),  # ipv6 unique-local
        ("::ffff:127.0.0.1", False),  # ipv4-mapped loopback
        ("::ffff:8.8.8.8", True),  # ipv4-mapped global
        ("not-an-ip", False),
    ],
)
def test_is_public_ip(ip, expected):
    assert is_public_ip(ip) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("ftp://example.com/x", False),  # non-http scheme
        ("http:///nohost", False),  # missing host
        ("http://127.0.0.1/hook", False),  # literal loopback — no DNS needed
        ("http://169.254.169.254/latest/meta-data", False),  # metadata endpoint
        ("https://10.1.2.3/hook", False),  # literal private
    ],
)
def test_is_public_url_blocks(url, expected):
    # Literal-IP and scheme/host cases resolve without touching real DNS.
    assert is_public_url(url) is expected


@pytest.mark.parametrize(
    "url",
    [
        "http://user:secret@example.com/spoolman",
        "http://example.com/\x00spoolman",
        "file:///etc/passwd",
    ],
)
def test_normalize_http_url_rejects_unsafe_syntax(url):
    with pytest.raises(UnsafeUrlError):
        normalize_http_url(url)


# ---------------------------------------------------------------------------
# DNS rebinding: validate once, connect to the address that was validated
# ---------------------------------------------------------------------------


def _fake_getaddrinfo(*answers: str):
    """getaddrinfo stub that returns a different answer on each call."""
    calls = {"n": 0}

    def _resolver(host, port, *args, **kwargs):
        idx = min(calls["n"], len(answers) - 1)
        calls["n"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[idx], port or 80))]

    return _resolver


def test_resolve_public_target_rejects_private_answer(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))
    with pytest.raises(UnsafeUrlError) as exc:
        resolve_public_target("http://rebind.example/hook")
    assert exc.value.reason == "url_target_not_public"


def test_resolve_public_target_rejects_mixed_answers(monkeypatch):
    """A host answering with a public *and* a private address is an attack."""

    def _resolver(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _resolver)
    with pytest.raises(UnsafeUrlError):
        resolve_public_target("http://mixed.example/hook")


def test_resolve_public_target_pins_the_validated_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    target = resolve_public_target("https://good.example/hook")
    assert target.ip == "93.184.216.34"
    assert target.host == "good.example"
    assert target.port == 443


@pytest.mark.anyio
async def test_pinned_transport_dials_validated_ip_after_dns_flips(monkeypatch):
    """The rebind: DNS says public at validation time, loopback at connect time.

    The transport must still dial the validated address, and it must hand the
    real hostname down the stack so TLS verification is unaffected.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    target = resolve_public_target("http://rebind.example/hook")

    # From here on, DNS is hostile.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))

    dialled: list[tuple[str, int]] = []

    async def _fake_connect(self, host, port, **kwargs):  # noqa: ANN001
        dialled.append((host, port))
        raise RuntimeError("stop here — the peer is all we need to observe")

    monkeypatch.setattr(AnyIOBackend, "connect_tcp", _fake_connect)

    transport = pinned_transport(target)
    async with httpx.AsyncClient(transport=transport) as client:
        # The RuntimeError raised by _fake_connect above may arrive wrapped in
        # an httpx/anyio-internal exception type; only the dialled address
        # (asserted below) matters here, not which exception surfaces.
        with pytest.raises(Exception):  # noqa: B017
            await client.get(target.url)

    assert dialled == [("93.184.216.34", 80)], (
        "connected to the rebound address instead of the validated one"
    )


def test_pinned_sync_transport_dials_validated_ip_after_dns_flips(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    target = resolve_public_target("http://rebind.example/hook")
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))

    dialled: list[tuple[str, int]] = []

    def _fake_connect(self, host, port, **kwargs):  # noqa: ANN001
        dialled.append((host, port))
        raise RuntimeError("stop after observing the peer")

    monkeypatch.setattr(SyncBackend, "connect_tcp", _fake_connect)
    with httpx.Client(transport=pinned_sync_transport(target)) as client:
        with pytest.raises(Exception):  # noqa: B017
            client.get(target.url)

    assert dialled == [("93.184.216.34", 80)]
