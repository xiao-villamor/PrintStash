"""Defends ``test_revalidates_every_retry_and_uses_bounded_jitter`` behavior for the ``services`` production unit.

A failure means this boundary no longer preserves its observable contract.
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


@pytest.mark.anyio
async def test_revalidates_every_retry_and_uses_bounded_jitter() -> None:
    sender, seen = _sender(
        [
            httpx.Response(429, headers={"Retry-After": "120"}),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    resolved: list[str] = []
    delays: list[float] = []

    def resolve(url: str) -> PinnedTarget:
        resolved.append(url)
        return _target(url)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    transport = ProviderTransport(
        resolver=resolve,
        sender=sender,
        sleep=sleep,
        random=lambda: 0.5,
    )

    response = await transport.request("GET", "https://api.example/models/1")

    assert response.status_code == 200
    assert resolved == ["https://api.example/models/1"] * 3
    assert seen == [("https://api.example/models/1", "GET")] * 3
    assert delays == [10.0, 0.5]


@pytest.mark.anyio
async def test_redirect_is_revalidated_before_following() -> None:
    sender, seen = _sender(
        [
            httpx.Response(302, headers={"Location": "https://api.example/models/2"}),
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
async def test_rejects_redirect_outside_an_explicit_provider_allowlist() -> None:
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
async def test_does_not_retry_permanent_client_failures() -> None:
    sender, seen = _sender([httpx.Response(401)])
    transport = ProviderTransport(resolver=_target, sender=sender)

    response = await transport.request("GET", "https://api.example/models/1")

    assert response.status_code == 401
    assert len(seen) == 1


@pytest.mark.anyio
async def test_retries_transient_network_failures_with_deterministic_backoff() -> None:
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
async def test_network_retry_exhaustion_has_a_stable_secret_free_error(
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


@pytest.mark.anyio
async def test_streaming_response_aborts_before_buffering_the_body_cap() -> None:
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
async def test_host_limit_is_process_wide_across_transport_instances() -> None:
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


@pytest.mark.anyio
async def test_pooled_clients_close_at_the_transport_lifecycle_boundary(
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
    monkeypatch.setattr(transport_module, "pinned_transport", lambda _target: object())

    first = ProviderTransport(resolver=_target, max_attempts=1)
    second = ProviderTransport(resolver=_target, max_attempts=1)
    await first.request("GET", "https://api.example/models/1")
    await second.request("GET", "https://api.example/models/2")

    await close_provider_transport()

    assert len(clients) == 1
    assert all(client.closed for client in clients)


async def _no_sleep() -> None:
    pass
