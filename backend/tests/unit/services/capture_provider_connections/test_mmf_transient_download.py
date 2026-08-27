"""Defends ``test_mmf_file_url_is_transient_and_requires_public_https`` behavior for the ``capture_provider_connections`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.capture_provider_connections import (
    MyMiniFactoryMetadataClient,
    MyMiniFactoryTokens,
    ProviderConnectionError,
)


class _Transport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def request(self, method: str, url: str, **_kwargs: object) -> httpx.Response:
        self.calls.append(url)
        return httpx.Response(
            200, json=self.payload, request=httpx.Request(method, url)
        )


def test_mmf_file_url_is_transient_and_requires_public_https() -> None:
    transport = _Transport(
        {"download_url": "https://downloads.example.test/signed?token=secret"}
    )
    client = MyMiniFactoryMetadataClient(transport)
    url = asyncio.run(
        client.file_download_url("file-1", MyMiniFactoryTokens("access", "refresh", 60))
    )
    assert url.endswith("token=secret")
    assert transport.calls == ["https://www.myminifactory.com/api/v2/files/file-1"]
    assert "download_url" not in repr(client)


@pytest.mark.parametrize("url", ["http://example.test/file", "https://localhost/file"])
def test_mmf_file_url_rejects_non_public_or_non_https(url: str) -> None:
    client = MyMiniFactoryMetadataClient(_Transport({"download_url": url}))
    with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
        asyncio.run(
            client.file_download_url("file-1", MyMiniFactoryTokens("a", "b", 60))
        )
