"""A signed download URL is a credential with a short life.

MyMiniFactory returns a pre-signed URL to fetch a file. Two properties follow, and
both are asserted here: it is **transient**, so it is used immediately rather than
stored anywhere it could leak or outlive its validity, and it must be **public
HTTPS** — a non-public host means the download is being aimed back inside the
network, and plain HTTP means the credential travels in clear text.
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


class TestFileDownloadUrl:
    def test_an_mmf_file_url_is_refused_unless_it_is_public_https(self) -> None:
        transport = _Transport(
            {"download_url": "https://downloads.example.test/signed?token=secret"}
        )
        client = MyMiniFactoryMetadataClient(transport)
        url = asyncio.run(
            client.file_download_url(
                "file-1", MyMiniFactoryTokens("access", "refresh", 60)
            )
        )
        assert url.endswith("token=secret")
        assert transport.calls == ["https://www.myminifactory.com/api/v2/files/file-1"]
        assert "download_url" not in repr(client)

    @pytest.mark.parametrize(
        "url", ["http://example.test/file", "https://localhost/file"]
    )
    def test_mmf_file_url_rejects_non_public_or_non_https(self, url: str) -> None:
        client = MyMiniFactoryMetadataClient(_Transport({"download_url": url}))
        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            asyncio.run(
                client.file_download_url("file-1", MyMiniFactoryTokens("a", "b", 60))
            )
