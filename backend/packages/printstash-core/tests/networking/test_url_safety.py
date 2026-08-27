"""Defends ``test_is_public_ip`` behavior for the ``networking`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import socket

import httpx
import pytest
from httpcore import AnyIOBackend, SyncBackend

from printstash_core.networking import (
    UnsafeUrlError,
    is_public_ip,
    normalize_http_url,
    pinned_sync_transport,
    pinned_transport,
    resolve_public_target,
)


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("8.8.8.8", True),
        ("127.0.0.1", False),
        ("10.0.0.1", False),
        ("100.64.0.1", False),
        ("198.18.0.1", False),
        ("192.0.2.1", False),
        ("::1", False),
        ("fc00::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:8.8.8.8", True),
        ("not-an-ip", False),
    ],
)
def test_is_public_ip(ip: str, expected: bool) -> None:
    assert is_public_ip(ip) is expected


@pytest.mark.parametrize(
    "url",
    [
        "http://user:secret@example.com/spoolman",
        "http://example.com/\x00spoolman",
        "file:///etc/passwd",
    ],
)
def test_normalize_http_url_rejects_unsafe_syntax(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        normalize_http_url(url)


def _fake_getaddrinfo(*answers: str):
    calls = {"count": 0}

    def _resolver(host: str, port: int, *args: object, **kwargs: object):
        index = min(calls["count"], len(answers) - 1)
        calls["count"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[index], port))]

    return _resolver


def test_resolution_normalizes_and_rejects_mixed_answers(monkeypatch) -> None:
    def _resolver(host: str, port: int, *args: object, **kwargs: object):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _resolver)
    with pytest.raises(UnsafeUrlError, match="url_target_not_public"):
        resolve_public_target(" HTTP://Mixed.Example/hook ")


@pytest.mark.anyio
async def test_async_transport_dials_validated_address(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    target = resolve_public_target("http://rebind.example/hook")
    dialled: list[tuple[str, int]] = []

    async def _connect(self, host: str, port: int, **kwargs: object):
        dialled.append((host, port))
        raise RuntimeError("stop")

    monkeypatch.setattr(AnyIOBackend, "connect_tcp", _connect)
    async with httpx.AsyncClient(transport=pinned_transport(target)) as client:
        with pytest.raises(Exception):  # noqa: B017
            await client.get(target.url)
    assert dialled == [("93.184.216.34", 80)]


def test_sync_transport_dials_validated_address(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    target = resolve_public_target("http://rebind.example/hook")
    dialled: list[tuple[str, int]] = []

    def _connect(self, host: str, port: int, **kwargs: object):
        dialled.append((host, port))
        raise RuntimeError("stop")

    monkeypatch.setattr(SyncBackend, "connect_tcp", _connect)
    with httpx.Client(transport=pinned_sync_transport(target)) as client:
        with pytest.raises(Exception):  # noqa: B017
            client.get(target.url)
    assert dialled == [("93.184.216.34", 80)]
