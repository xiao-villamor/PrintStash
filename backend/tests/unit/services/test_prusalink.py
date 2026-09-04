"""The legacy constructor still in front of the extracted PrusaLink client.

The client moved into `printstash-core` and is tested there against a mock
transport (`printers/test_prusalink.py`) and in this repo's contract tier against
the real emulator. What remains here is the constructor callers had before the
extraction — and for PrusaLink that translation carries more than a URL.

PrusaLink speaks two authentication schemes: an API key header, and HTTP digest
with a username and password. The legacy signature takes `auth_mode` plus four
optional credential fields and has to funnel them into the core config that
decides which scheme the transport uses. Getting that wrong does not fail loudly;
it produces a client that authenticates the wrong way and reports every printer
as unreachable, which reads as a network problem rather than a wiring bug.
"""

from __future__ import annotations

import httpx
import pytest
from printstash_core.printers.prusalink import PrusaLinkClient as CorePrusaLinkClient
from printstash_core.printers.prusalink import PrusaLinkError as CorePrusaLinkError

from app.services.prusalink import PrusaLinkClient, PrusaLinkError

API_KEY = "key-123"
USERNAME = "maker"
PASSWORD = "hunter2"


def _client(
    auth_mode: str, seen: list[httpx.Request], **credentials: str
) -> PrusaLinkClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"printer": {"state": "IDLE"}})

    return PrusaLinkClient(
        "http://printer.local",
        auth_mode=auth_mode,
        transport=httpx.MockTransport(handler),
        **credentials,
    )


class TestPrusaLinkClient:
    def test_builds_a_core_client_from_the_legacy_arguments(self) -> None:
        client = PrusaLinkClient(
            "http://printer.local", auth_mode="api_key", api_key=API_KEY
        )

        assert isinstance(client, CorePrusaLinkClient)

    @pytest.mark.asyncio
    async def test_sends_the_api_key_header_in_api_key_mode(self) -> None:
        seen: list[httpx.Request] = []
        client = _client("api_key", seen, api_key=API_KEY)

        await client.query_status()

        assert seen[0].headers["X-Api-Key"] == API_KEY

    @pytest.mark.asyncio
    async def test_omits_the_api_key_header_in_digest_mode(self) -> None:
        seen: list[httpx.Request] = []
        client = _client("digest", seen, username=USERNAME, password=PASSWORD)

        await client.query_status()

        # Digest credentials travel in the challenge response, not a header. A
        # facade that passed them through as an API key would authenticate the
        # wrong way and report the printer as unreachable.
        assert "X-Api-Key" not in seen[0].headers

    def test_strips_a_trailing_slash_from_the_configured_url(self) -> None:
        client = PrusaLinkClient(
            "http://printer.local/", auth_mode="api_key", api_key=API_KEY
        )

        assert client.base_url == "http://printer.local"

    def test_re_exports_the_error_class_core_actually_raises(self) -> None:
        # Call sites `except PrusaLinkError`. An alias that drifted from core's
        # class would turn every provider failure into an unhandled 500.
        assert PrusaLinkError is CorePrusaLinkError
