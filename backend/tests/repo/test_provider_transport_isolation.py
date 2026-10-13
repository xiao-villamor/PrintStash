"""Provider clients from a previous test cannot be closed on a new loop."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.modules.ingestion import capture_provider_transport as transport
from tests import conftest


class LoopBoundTransport(httpx.AsyncBaseTransport):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.closed = False

    async def aclose(self) -> None:
        self.loop.call_soon(lambda: None)
        self.closed = True


class TestProviderTransportIsolation:
    def test_new_test_does_not_close_a_client_from_an_ended_loop(
        self, monkeypatch, tmp_path_factory
    ):
        old_loop = asyncio.new_event_loop()
        old_transport = LoopBoundTransport(old_loop)
        old_client = httpx.AsyncClient(transport=old_transport)
        old_loop.close()
        monkeypatch.setattr(
            transport,
            "_pooled_clients",
            {("old", 443, "https", "ip", id(old_loop)): old_client},
        )

        conftest._patch_engine.__wrapped__(monkeypatch, tmp_path_factory)
        asyncio.run(transport.close_provider_transport())

        assert not old_transport.closed
        assert not old_client.is_closed
        assert not transport._pooled_clients

    def test_new_test_starts_with_available_host_capacity(
        self, monkeypatch, tmp_path_factory
    ):
        limiter = asyncio.Semaphore(2)
        asyncio.run(limiter.acquire())
        asyncio.run(limiter.acquire())
        assert limiter.locked()
        monkeypatch.setattr(transport, "_host_limiters", {"api.example": limiter})

        conftest._patch_engine.__wrapped__(monkeypatch, tmp_path_factory)

        assert not transport._get_host_limiter("api.example").locked()

    def test_current_test_closes_its_own_client_on_its_live_loop(self):
        async def run():
            current_transport = LoopBoundTransport(asyncio.get_running_loop())
            client = httpx.AsyncClient(transport=current_transport)
            transport._pooled_clients[
                ("new", 443, "https", "ip", id(asyncio.get_running_loop()))
            ] = client
            await transport.close_provider_transport()
            assert client.is_closed
            assert current_transport.closed
            assert not transport._pooled_clients

        asyncio.run(run())

    def test_current_test_close_failure_is_not_suppressed(self):
        old_loop = asyncio.new_event_loop()
        current_transport = LoopBoundTransport(old_loop)
        client = httpx.AsyncClient(transport=current_transport)
        old_loop.close()
        transport._pooled_clients[("new", 443, "https", "ip", id(old_loop))] = client

        with pytest.raises(RuntimeError, match="Event loop is closed"):
            asyncio.run(transport.close_provider_transport())
        assert not current_transport.closed
