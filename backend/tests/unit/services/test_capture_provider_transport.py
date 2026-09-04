"""Fetching from a provider we do not control, with the SSRF guard held open.

This transport downloads bytes from a third-party host on the user's behalf, and
its whole reason for existing is that a single validation is not enough. Three
things happen after the first check that can each move the target:

* **A retry** re-resolves the hostname. So every attempt is revalidated, not just
  the first — otherwise a retry is a free second chance at DNS rebinding.
* **A redirect** names a new URL entirely. It is revalidated before being
  followed, and only within an explicit provider allowlist: a 302 to
  `169.254.169.254` is the cloud metadata service, and following it once is
  enough.
* **A hostile provider** can simply not answer. Backoff is bounded and jittered,
  and the jitter is deterministic under test so the assertion is on the schedule
  rather than on a sleep.

The retry *policy* matters as much as the safety. A permanent client failure is
not retried — replaying a 400 sends the same bad request again — while a
transient network failure is, up to a limit, after which the error is stable and
**free of the signed URL**, because that error reaches a log and a job record.

The host limit is process-wide rather than per-instance: a per-instance cap is no
cap at all when the caller constructs a transport per request.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.core.url_safety import PinnedTarget
from app.services import capture_provider_transport as transport_module
from app.services.capture_provider_transport import (
    ProviderTransport,
    ProviderTransportError,
    close_provider_transport,
)


def _target(url: str) -> PinnedTarget:
    return PinnedTarget(url=url, host="api.example", port=443, ip="93.184.216.34")


def _sender(responses: list[httpx.Response]):
    seen: list[tuple[str, str]] = []

    async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
        seen.append((target.url, request.method))
        response = responses.pop(0)
        response.request = request
        return response

    return send, seen


_URL = "https://api.example/models/1"


def _retrying_transport() -> tuple[ProviderTransport, dict[str, list]]:
    """A transport whose first two attempts fail, with every side effect recorded.

    429 with an over-long `Retry-After`, then a bare 503, then success — the two
    distinct backoff paths in one sequence, which is why both tests below share it.
    """
    sender, seen = _sender(
        [
            httpx.Response(429, headers={"Retry-After": "120"}),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    state: dict[str, list] = {"resolved": [], "delays": [], "seen": seen}

    def resolve(url: str) -> PinnedTarget:
        state["resolved"].append(url)
        return _target(url)

    async def sleep(delay: float) -> None:
        state["delays"].append(delay)

    transport = ProviderTransport(
        resolver=resolve, sender=sender, sleep=sleep, random=lambda: 0.5
    )
    return transport, state


class TestRequest:
    """One outbound provider request, and every way it must refuse to become another."""

    @pytest.mark.anyio
    async def test_revalidates_the_target_on_every_retry(self) -> None:
        # Re-resolving each time is what stops a DNS record from being rebound to a
        # private address between the first attempt and the retry.
        transport, state = _retrying_transport()

        await transport.request("GET", _URL)

        assert state["resolved"] == [_URL] * 3
        assert state["seen"] == [(_URL, "GET")] * 3

    @pytest.mark.asyncio
    async def test_honours_retry_after_then_falls_back_to_bounded_jitter(self) -> None:
        transport, state = _retrying_transport()

        response = await transport.request("GET", _URL)

        assert response.status_code == 200
        # 10.0 is the capped `Retry-After: 120`; 0.5 is the jittered backoff for the
        # 503, with `random` pinned so the bound is checkable at all.
        assert state["delays"] == [10.0, 0.5]

    @pytest.mark.anyio
    async def test_redirect_is_revalidated_before_following(self) -> None:
        sender, seen = _sender(
            [
                httpx.Response(
                    302, headers={"Location": "https://api.example/models/2"}
                ),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        resolved: list[str] = []
        transport = ProviderTransport(
            resolver=lambda url: (resolved.append(url), _target(url))[1], sender=sender
        )

        response = await transport.request("GET", "https://api.example/models/1")

        assert response.status_code == 200
        assert resolved == [
            "https://api.example/models/1",
            "https://api.example/models/2",
        ]
        assert seen == [
            ("https://api.example/models/1", "GET"),
            ("https://api.example/models/2", "GET"),
        ]

    @pytest.mark.anyio
    async def test_rejects_redirect_outside_an_explicit_provider_allowlist(
        self,
    ) -> None:
        sender, _ = _sender(
            [httpx.Response(302, headers={"Location": "https://evil.example/next"})]
        )
        transport = ProviderTransport(resolver=_target, sender=sender)

        with pytest.raises(ProviderTransportError) as exc:
            await transport.request(
                "GET",
                "https://api.example/models/1",
                allowed_hosts=frozenset({"api.example"}),
            )

        assert exc.value.code == "provider_endpoint_not_allowed"
        assert not exc.value.retryable

    @pytest.mark.anyio
    async def test_refuses_a_redirect_with_no_location(self) -> None:
        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, request=request)

        transport = ProviderTransport(resolver=_target, sender=send, max_attempts=2)

        # A redirect with nowhere to go is a broken provider, not a retry.
        with pytest.raises(ProviderTransportError, match="provider_redirect_invalid"):
            await transport.request("GET", "https://api.example/redirect")

    @pytest.mark.anyio
    async def test_gives_up_on_a_redirect_chain_that_is_too_long(self) -> None:
        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302, headers={"Location": "https://api.example/again"}, request=request
            )

        transport = ProviderTransport(resolver=_target, sender=send, max_attempts=1)

        with pytest.raises(ProviderTransportError, match="provider_retry_exhausted"):
            await transport.request("GET", "https://api.example/loop")

    @pytest.mark.anyio
    async def test_turns_a_303_redirect_into_a_get(self) -> None:
        seen: list[tuple[str, str]] = []

        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            seen.append((request.method, target.url))
            if len(seen) == 1:
                return httpx.Response(
                    303,
                    headers={"Location": "https://api.example/result"},
                    request=request,
                )
            return httpx.Response(200, content=b"{}", request=request)

        transport = ProviderTransport(resolver=_target, sender=send, max_attempts=2)

        await transport.request("POST", "https://api.example/submit", json={"a": 1})

        # A 303 means "look over there with a GET"; replaying the POST body would
        # submit the same thing twice.
        assert [method for method, _url in seen] == ["POST", "GET"]

    @pytest.mark.anyio
    async def test_refuses_a_url_outside_an_explicit_allowlist(self) -> None:
        transport = ProviderTransport(resolver=_target, sender=_sender([])[0])

        with pytest.raises(
            ProviderTransportError, match="provider_endpoint_not_allowed"
        ):
            await transport.request(
                "GET",
                "https://elsewhere.example/x",
                allowed_hosts=frozenset({"api.example"}),
            )

    @pytest.mark.anyio
    async def test_refuses_a_url_with_no_host_against_an_allowlist(self) -> None:
        transport = ProviderTransport(resolver=_target, sender=_sender([])[0])

        with pytest.raises(
            ProviderTransportError, match="provider_endpoint_not_allowed"
        ):
            await transport.request(
                "GET", "not-a-url", allowed_hosts=frozenset({"api.example"})
            )

    @pytest.mark.anyio
    async def test_retries_transient_network_failures_with_deterministic_backoff(
        self,
    ) -> None:
        seen: list[str] = []
        delays: list[float] = []

        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            seen.append(target.url)
            if len(seen) < 3:
                raise httpx.ReadTimeout("upstream timed out", request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        async def sleep(delay: float) -> None:
            delays.append(delay)

        transport = ProviderTransport(
            resolver=_target, sender=send, sleep=sleep, random=lambda: 1.0
        )

        response = await transport.request("GET", "https://api.example/models/1")

        assert response.status_code == 200
        assert seen == ["https://api.example/models/1"] * 3
        assert delays == [0.25, 1.0]

    @pytest.mark.anyio
    async def test_does_not_retry_permanent_client_failures(self) -> None:
        sender, seen = _sender([httpx.Response(401)])
        transport = ProviderTransport(resolver=_target, sender=sender)

        response = await transport.request("GET", "https://api.example/models/1")

        assert response.status_code == 401
        assert len(seen) == 1

    @pytest.mark.anyio
    async def test_network_retry_exhaustion_has_a_stable_secret_free_error(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("token=top-secret", request=request)

        transport = ProviderTransport(
            resolver=_target, sender=send, sleep=lambda _: _no_sleep()
        )

        with pytest.raises(ProviderTransportError) as exc:
            await transport.request(
                "GET",
                "https://user:password@api.example/models/1?X-Amz-Signature=top-secret",
            )

        assert exc.value.code == "provider_retry_exhausted"
        assert str(exc.value) == "provider_retry_exhausted"
        assert "top-secret" not in repr(exc.value)
        assert "top-secret" not in caplog.text
        assert "user:password" not in caplog.text
        assert "api.example/models/1" in caplog.text


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class TestCloseProviderTransport:
    @pytest.mark.anyio
    async def test_pooled_clients_close_at_the_transport_lifecycle_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await close_provider_transport()
        clients: list[Any] = []

        class Client:
            def __init__(self, **_kwargs: object) -> None:
                self.closed = False
                clients.append(self)

            @property
            def is_closed(self) -> bool:
                return self.closed

            async def send(
                self,
                request: httpx.Request,
                **_kwargs: object,
            ) -> httpx.Response:
                return httpx.Response(200, content=b"ok", request=request)

            async def aclose(self) -> None:
                self.closed = True

        monkeypatch.setattr(transport_module.httpx, "AsyncClient", Client)
        monkeypatch.setattr(
            transport_module, "pinned_transport", lambda _target: object()
        )

        first = ProviderTransport(resolver=_target, max_attempts=1)
        second = ProviderTransport(resolver=_target, max_attempts=1)
        await first.request("GET", "https://api.example/models/1")
        await second.request("GET", "https://api.example/models/2")

        await close_provider_transport()

        assert len(clients) == 1
        assert all(client.closed for client in clients)


async def _no_sleep() -> None:
    pass


class TestNormalizedHost:
    """Host-wide traffic limits only work if two spellings of a host are one key."""

    @pytest.mark.parametrize(
        "value",
        ["EXAMPLE.TEST", "example.test.", " example.test ", "Example.Test."],
        ids=["uppercase", "trailing-dot", "whitespace", "mixed"],
    )
    def test_folds_every_spelling_of_one_host_together(self, value: str) -> None:
        # A limiter keyed on the raw string would let a provider bypass its own
        # concurrency cap by varying the case of its hostname.
        assert transport_module._normalized_host(value) == "example.test"

    def test_normalises_an_international_host_to_punycode(self) -> None:
        assert transport_module._normalized_host("exämple.test").startswith("xn--")

    def test_falls_back_when_a_host_cannot_be_encoded(self) -> None:
        # An unencodable host still gets *a* key, so it is still rate-limited,
        # rather than escaping the limiter entirely.
        assert transport_module._normalized_host("A" * 300) == "a" * 300


class TestHostLimiter:
    def test_reuses_one_limiter_per_host(self) -> None:
        first = transport_module._get_host_limiter("limiter.example")
        second = transport_module._get_host_limiter("limiter.example")

        assert first is second

    def test_reuses_it_across_spellings_of_the_same_host(self) -> None:
        first = transport_module._get_host_limiter("Spellings.Example")
        second = transport_module._get_host_limiter("spellings.example.")

        assert first is second

    def test_gives_a_different_host_its_own_limiter(self) -> None:
        first = transport_module._get_host_limiter("one.example")
        second = transport_module._get_host_limiter("two.example")

        assert first is not second

    @pytest.mark.anyio
    async def test_host_limit_is_process_wide_across_transport_instances(self) -> None:
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        def target(url: str) -> PinnedTarget:
            return PinnedTarget(
                url=url,
                host="shared-provider.example",
                port=443,
                ip="93.184.216.34",
            )

        async def send(_target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            nonlocal active, max_active
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            async with lock:
                active -= 1
            return httpx.Response(200, content=b"ok", request=request)

        first = ProviderTransport(resolver=target, sender=send, max_attempts=1)
        second = ProviderTransport(resolver=target, sender=send, max_attempts=1)
        responses = await asyncio.gather(
            *[
                transport.request("GET", f"https://shared-provider.example/{index}")
                for index, transport in enumerate(
                    [first, second, first, second, first, second]
                )
            ]
        )

        assert [response.status_code for response in responses] == [200] * 6
        assert max_active == 2


class TestReadBoundedResponse:
    """Reading a body a provider chose the length of."""

    @pytest.mark.anyio
    async def test_refuses_a_body_whose_declared_length_is_over_the_cap(self) -> None:
        """Refuse before reading, when the server tells us how big the body is."""
        stream = _ChunkStream([b"never read"])

        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Length": "1000"},
                stream=stream,
                request=request,
            )

        transport = ProviderTransport(
            resolver=_target, sender=send, max_attempts=1, max_response_bytes=5
        )

        with pytest.raises(ProviderTransportError, match="provider_response_too_large"):
            await transport.request("GET", "https://api.example/big")
        assert stream.yielded == 0

    @pytest.mark.anyio
    async def test_ignores_a_declared_length_it_cannot_parse(self) -> None:
        """A malformed header is not a reason to trust the body — the loop still caps it."""
        stream = _ChunkStream([b"1234", b"5678"])

        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Length": "not-a-number"},
                stream=stream,
                request=request,
            )

        transport = ProviderTransport(
            resolver=_target, sender=send, max_attempts=1, max_response_bytes=5
        )

        with pytest.raises(ProviderTransportError, match="provider_response_too_large"):
            await transport.request("GET", "https://api.example/lying")

    @pytest.mark.anyio
    async def test_closes_the_stream_when_reading_it_fails(self) -> None:
        """A connection that drops mid-body must not leak its pooled connection."""

        class _Failing(httpx.AsyncByteStream):
            def __init__(self) -> None:
                self.closed = False

            async def __aiter__(self):
                yield b"1"
                raise httpx.ReadError("connection reset")

            async def aclose(self) -> None:
                self.closed = True

        stream = _Failing()

        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=stream, request=request)

        transport = ProviderTransport(
            resolver=_target, sender=send, max_attempts=1, max_response_bytes=1000
        )

        with pytest.raises(ProviderTransportError):
            await transport.request("GET", "https://api.example/drops")
        assert stream.closed is True


class TestConstructorValidation:
    """The transport's own limits are validated at construction, not at call time."""

    @pytest.mark.parametrize(
        "override",
        [
            pytest.param({"max_attempts": 0}, id="attempts-zero"),
            pytest.param({"max_attempts": 4}, id="attempts-over-cap"),
            pytest.param({"concurrency": 0}, id="concurrency-zero"),
            pytest.param({"concurrency": 5}, id="concurrency-over-cap"),
            pytest.param({"retry_after_max_seconds": -1}, id="retry-after-negative"),
            pytest.param({"retry_after_max_seconds": 11}, id="retry-after-over-cap"),
            pytest.param({"max_response_bytes": 0}, id="body-cap-zero"),
        ],
    )
    def test_refuses_a_limit_outside_its_bounds(self, override: dict) -> None:
        # Caught at construction, so a misconfigured transport cannot reach a
        # provider at all rather than reaching it three hundred times.
        with pytest.raises(ValueError):
            ProviderTransport(resolver=_target, sender=_sender([])[0], **override)


class TestRetryAfter:
    """Reading a provider's `Retry-After`, in either of the two forms it takes."""

    def test_reads_a_delay_in_seconds(self) -> None:
        assert transport_module._retry_after_seconds("5") == 5.0

    def test_reads_a_delay_given_as_a_date(self) -> None:
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime

        later = datetime.now(timezone.utc) + timedelta(seconds=30)

        value = transport_module._retry_after_seconds(format_datetime(later))

        assert value is not None
        assert 0 < value <= 31

    def test_never_returns_a_negative_delay(self) -> None:
        # A date in the past means "now", not "go back in time".
        assert transport_module._retry_after_seconds("-5") == 0.0

    @pytest.mark.parametrize(
        "value", ["", "soon", "not a date"], ids=["empty", "prose", "bad-date"]
    )
    def test_says_it_could_not_read_one(self, value: str) -> None:
        assert transport_module._retry_after_seconds(value) is None


class TestResponse:
    @pytest.mark.anyio
    async def test_streaming_response_aborts_before_buffering_the_body_cap(
        self,
    ) -> None:
        stream = _ChunkStream([b"1234", b"56", b"this must not be read"])

        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=stream, request=request)

        transport = ProviderTransport(
            resolver=_target,
            sender=send,
            max_attempts=1,
            max_response_bytes=5,
        )

        with pytest.raises(ProviderTransportError) as exc:
            await transport.request("GET", "https://api.example/models/1")

        assert exc.value.code == "provider_response_too_large"
        assert stream.closed
        assert stream.yielded == 2

    @pytest.mark.anyio
    async def test_returns_a_buffered_response_the_caller_can_read_twice(self) -> None:
        """The streamed response is replaced, not handed on, so its connection is freed."""
        stream = _ChunkStream([b"12", b"34"])

        async def send(target: PinnedTarget, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=stream, request=request)

        transport = ProviderTransport(
            resolver=_target, sender=send, max_attempts=1, max_response_bytes=1000
        )

        response = await transport.request("GET", "https://api.example/ok")

        assert response.content == b"1234"
        assert response.content == b"1234"
        assert stream.closed is True
