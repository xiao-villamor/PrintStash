"""Stopping a server-side fetch from being aimed back at the machine it runs on.

Several PrintStash features take a URL from a user and fetch it *from the server*:
a Spoolman endpoint, a notification webhook, a model page to capture. On a
self-hosted box that server sits inside the user's home network, next to the
router's admin page, the NAS, and the cloud metadata service if it is hosted. So
an unchecked fetch is a Server-Side Request Forgery primitive handed to whoever
can type a URL into the settings form.

The defence has three parts, and this file covers each because skipping any one
of them defeats the other two:

**Syntax.** Reject before resolving — a control character, a credential in the
userinfo, a non-HTTP scheme. These are cheap and they remove whole classes of
smuggling before DNS is involved.

**Resolution.** Resolve once, and reject if *any* answer is non-public. Not the
first answer: a hostname that resolves to one public and one loopback address is
an attack, and taking the first would pass it.

**Pinning.** Dial the exact address that was validated. This is the part people
leave out, and without it the check is theatre: between the validation and the
connection, the attacker's DNS server answers again with `127.0.0.1`. That is
DNS rebinding, and the transport tests below assert the socket is opened against
the validated *IP* rather than the hostname.

Every error carries a stable `reason`, because the API turns it into a code the
UI shows and a caller may branch on.
"""

from __future__ import annotations

import socket
from typing import Callable

import httpx
import pytest
from httpcore import AnyIOBackend, SyncBackend

from printstash_core.networking import (
    UnsafeUrlError,
    is_public_ip,
    is_public_url,
    normalize_http_url,
    pinned_sync_transport,
    pinned_transport,
    resolve_public_target,
)

PUBLIC_IP = "93.184.216.34"
LOOPBACK = "127.0.0.1"


def _resolver(*answers: str) -> Callable[..., list[tuple]]:
    """A `getaddrinfo` that returns *answers*, then repeats the last one."""
    calls = {"count": 0}

    def resolve(host: str, port: int, *args: object, **kwargs: object) -> list[tuple]:
        index = min(calls["count"], len(answers) - 1)
        calls["count"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[index], port))]

    return resolve


def _multi_resolver(*answers: str) -> Callable[..., list[tuple]]:
    """A `getaddrinfo` that returns every answer at once, as a real one can."""

    def resolve(host: str, port: int, *args: object, **kwargs: object) -> list[tuple]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer, port))
            for answer in answers
        ]

    return resolve


class TestIsPublicIp:
    @pytest.mark.parametrize(
        ("ip", "expected"),
        [
            pytest.param("8.8.8.8", True, id="public-v4"),
            pytest.param("::ffff:8.8.8.8", True, id="v4-mapped-public"),
            pytest.param("127.0.0.1", False, id="loopback"),
            pytest.param("10.0.0.1", False, id="rfc1918"),
            pytest.param("100.64.0.1", False, id="carrier-grade-nat"),
            pytest.param("198.18.0.1", False, id="benchmarking"),
            pytest.param("192.0.2.1", False, id="documentation"),
            pytest.param("::1", False, id="v6-loopback"),
            pytest.param("fc00::1", False, id="v6-unique-local"),
            pytest.param("::ffff:127.0.0.1", False, id="v4-mapped-loopback"),
            pytest.param("not-an-ip", False, id="not-an-address"),
        ],
    )
    def test_classifies_an_address(self, ip: str, expected: bool) -> None:
        # The v4-mapped pair is the one worth having twice: `::ffff:127.0.0.1`
        # reaches loopback while looking like a v6 address, so a check that only
        # inspected the family would wave it through.
        assert is_public_ip(ip) is expected


class TestNormalizeHttpUrl:
    def test_normalizes_an_authority_to_lower_case_with_a_root_path(self) -> None:
        assert normalize_http_url(" HTTP://Example.COM ") == "http://example.com/"

    def test_keeps_everything_that_changes_the_target(self) -> None:
        assert (
            normalize_http_url("https://example.com:8443/hook?a=1#frag")
            == "https://example.com:8443/hook?a=1#frag"
        )

    def test_encodes_an_international_host_as_idna(self) -> None:
        # The resolver and the allowlist both work on the ASCII form, so a
        # Unicode host has to be folded before either sees it.
        assert normalize_http_url("http://bücher.example/x") == (
            "http://xn--bcher-kva.example/x"
        )

    def test_brackets_a_bare_ipv6_host(self) -> None:
        assert normalize_http_url("http://[::ffff:8.8.8.8]/x").startswith("http://[")

    def test_strips_credentials_when_asked(self) -> None:
        # The Spoolman path accepts a URL a user pasted from a browser, which may
        # carry userinfo; stripping is opt-in so no other caller inherits it.
        assert (
            normalize_http_url(
                "http://user:secret@example.com/x", strip_credentials=True
            )
            == "http://example.com/x"
        )

    @pytest.mark.parametrize(
        ("url", "reason"),
        [
            pytest.param("", "url_invalid", id="empty"),
            pytest.param("   ", "url_invalid", id="whitespace-only"),
            pytest.param("http://example.com/\x00x", "url_invalid", id="null-byte"),
            pytest.param("http://example.com/\x7fx", "url_invalid", id="delete-char"),
            pytest.param("http://example.com/\nx", "url_invalid", id="newline"),
            pytest.param("file:///etc/passwd", "url_scheme_not_allowed", id="file"),
            pytest.param("ftp://example.com/x", "url_scheme_not_allowed", id="ftp"),
            pytest.param(
                "gopher://example.com/x", "url_scheme_not_allowed", id="gopher"
            ),
            pytest.param("http:///x", "url_host_missing", id="no-host"),
            pytest.param(
                "http://user:secret@example.com/x",
                "url_credentials_not_allowed",
                id="userinfo",
            ),
            pytest.param(
                "http://example.com:not-a-port/x", "url_invalid", id="unparseable-port"
            ),
            pytest.param(
                "http://" + "a" * 300 + ".example/x", "url_invalid", id="idna-too-long"
            ),
        ],
    )
    def test_refuses_unsafe_syntax_with_a_stable_reason(
        self, url: str, reason: str
    ) -> None:
        # The reason is part of the contract: the API turns it into a code the
        # settings form shows next to the field.
        with pytest.raises(UnsafeUrlError) as caught:
            normalize_http_url(url)

        assert caught.value.reason == reason

    def test_never_echoes_a_rejected_url(self) -> None:
        with pytest.raises(UnsafeUrlError) as caught:
            normalize_http_url("http://user:hunter2@example.com/x")

        # A rejected URL reaches a log and an error body, so the credential in it
        # must not travel with the exception.
        assert "hunter2" not in str(caught.value)


class TestResolvePublicTarget:
    def test_pins_the_resolved_address(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC_IP))

        target = resolve_public_target("http://example.com/hook")

        assert (target.url, target.host, target.port, target.ip) == (
            "http://example.com/hook",
            "example.com",
            80,
            PUBLIC_IP,
        )

    def test_defaults_the_port_from_the_scheme(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC_IP))

        assert resolve_public_target("https://example.com/hook").port == 443

    def test_keeps_an_explicit_port(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC_IP))

        assert resolve_public_target("http://example.com:8080/hook").port == 8080

    def test_refuses_when_any_answer_is_not_public(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _multi_resolver(PUBLIC_IP, LOOPBACK))

        # *Any*, not the first: a host that answers with one public and one
        # loopback address is an attack, and taking the first would pass it.
        with pytest.raises(UnsafeUrlError) as caught:
            resolve_public_target(" HTTP://Mixed.Example/hook ")

        assert caught.value.reason == "url_target_not_public"

    def test_refuses_when_the_only_answer_is_private(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver("192.168.1.1"))

        with pytest.raises(UnsafeUrlError, match="url_target_not_public"):
            resolve_public_target("http://nas.example/hook")

    def test_reports_a_dns_failure_as_its_own_reason(self, monkeypatch) -> None:
        def fail(*_args: object, **_kwargs: object):
            raise socket.gaierror("no such host")

        monkeypatch.setattr(socket, "getaddrinfo", fail)

        # Distinct from "not public": a typo in a hostname and a deliberate
        # attempt to reach the LAN are different messages for the user.
        with pytest.raises(UnsafeUrlError) as caught:
            resolve_public_target("http://nonexistent.example/hook")

        assert caught.value.reason == "url_dns_resolution_failed"

    def test_reports_an_empty_answer_as_a_dns_failure(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])

        # A resolver that returns nothing without raising would otherwise fall
        # through to "no answer was non-public", which is true and useless.
        with pytest.raises(UnsafeUrlError, match="url_dns_resolution_failed"):
            resolve_public_target("http://empty.example/hook")

    def test_applies_the_syntax_rules_before_resolving(self, monkeypatch) -> None:
        def fail(*_args: object, **_kwargs: object):
            raise AssertionError("must not resolve a URL that failed syntax checks")

        monkeypatch.setattr(socket, "getaddrinfo", fail)

        with pytest.raises(UnsafeUrlError, match="url_scheme_not_allowed"):
            resolve_public_target("file:///etc/passwd")

    def test_accepts_a_caller_supplied_validator(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(LOOPBACK))

        # The e2e suite relaxes this to let loopback through against its own
        # fakes; the seam exists so that relaxation is explicit and local.
        target = resolve_public_target(
            "http://localhost/hook", ip_validator=lambda _ip: True
        )

        assert target.ip == LOOPBACK


class TestIsPublicUrl:
    def test_is_true_for_a_public_target(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC_IP))

        assert is_public_url("http://example.com/hook") is True

    @pytest.mark.parametrize(
        ("url", "answer"),
        [
            pytest.param("http://nas.example/x", LOOPBACK, id="private-target"),
            pytest.param("file:///etc/passwd", PUBLIC_IP, id="bad-scheme"),
        ],
    )
    def test_is_false_for_anything_refused(
        self, monkeypatch, url: str, answer: str
    ) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(answer))

        # The boolean form swallows *every* reason, which is why callers that
        # need to tell the user why use `resolve_public_target` instead.
        assert is_public_url(url) is False


class TestPinnedTransport:
    """The half that makes the check real: dial the address that was validated."""

    @pytest.mark.anyio
    async def test_async_transport_dials_the_validated_address(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC_IP))
        target = resolve_public_target("http://rebind.example/hook")
        dialled: list[tuple[str, int]] = []

        async def connect(self, host: str, port: int, **kwargs: object):
            dialled.append((host, port))
            raise RuntimeError("stop before any bytes move")

        monkeypatch.setattr(AnyIOBackend, "connect_tcp", connect)

        async with httpx.AsyncClient(transport=pinned_transport(target)) as client:
            with pytest.raises(Exception):  # noqa: B017 — the stop signal above
                await client.get(target.url)

        # The IP, not the hostname. Passing the hostname here would resolve a
        # second time, which is the whole DNS-rebinding window.
        assert dialled == [(PUBLIC_IP, 80)]

    def test_sync_transport_dials_the_validated_address(self, monkeypatch) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC_IP))
        target = resolve_public_target("http://rebind.example/hook")
        dialled: list[tuple[str, int]] = []

        def connect(self, host: str, port: int, **kwargs: object):
            dialled.append((host, port))
            raise RuntimeError("stop before any bytes move")

        monkeypatch.setattr(SyncBackend, "connect_tcp", connect)

        with httpx.Client(transport=pinned_sync_transport(target)) as client:
            with pytest.raises(Exception):  # noqa: B017 — the stop signal above
                client.get(target.url)

        assert dialled == [(PUBLIC_IP, 80)]

    @pytest.mark.anyio
    async def test_async_transport_ignores_a_later_dns_answer(
        self, monkeypatch
    ) -> None:
        # Resolve public once, then have DNS start answering loopback — the
        # attacker's second answer. The pin must make it irrelevant.
        monkeypatch.setattr(socket, "getaddrinfo", _resolver(PUBLIC_IP, LOOPBACK))
        target = resolve_public_target("http://rebind.example/hook")
        dialled: list[tuple[str, int]] = []

        async def connect(self, host: str, port: int, **kwargs: object):
            dialled.append((host, port))
            raise RuntimeError("stop before any bytes move")

        monkeypatch.setattr(AnyIOBackend, "connect_tcp", connect)

        async with httpx.AsyncClient(transport=pinned_transport(target)) as client:
            with pytest.raises(Exception):  # noqa: B017 — the stop signal above
                await client.get(target.url)

        assert dialled == [(PUBLIC_IP, 80)]
