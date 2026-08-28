"""The legacy constructor still in front of the extracted OctoPrint client.

The client itself moved into `printstash-core` and is tested there, exhaustively,
against a mock transport (`printers/test_octoprint.py`) and against the real
emulator in this repo's contract tier. Duplicating those behaviours here would
mean two suites that drift, so what is left is the only thing this module still
owns: the constructor shape callers had before the extraction.

That shape matters because it is not the core's. Core takes an `OctoPrintConfig`;
callers here pass `base_url` positionally with a keyword `api_key`. If the
translation between them is wrong, every printer in an existing installation
fails to build — and it fails at construction, far from anything that looks like
a provider bug. The re-exported `OctoPrintError` is part of the same contract:
call sites catch the app symbol, so it has to be the class core actually raises.
"""

from __future__ import annotations

import httpx
import pytest
from printstash_core.printers.octoprint import OctoPrintClient as CoreOctoPrintClient
from printstash_core.printers.octoprint import OctoPrintError as CoreOctoPrintError

from app.services.octoprint import OctoPrintClient, OctoPrintError

API_KEY = "key-123"


class TestOctoPrintClient:
    def test_builds_a_core_client_from_the_legacy_arguments(self) -> None:
        client = OctoPrintClient("http://printer.local", api_key=API_KEY)

        assert isinstance(client, CoreOctoPrintClient)

    @pytest.mark.asyncio
    async def test_sends_the_configured_api_key_to_the_configured_url(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        client = OctoPrintClient(
            "http://printer.local",
            api_key=API_KEY,
            transport=httpx.MockTransport(handler),
        )

        await client.info()

        # The whole point of the facade: `base_url` and `api_key` have to land
        # where the core config puts them, or every existing printer row builds
        # a client that talks to nowhere with no credentials.
        assert str(seen[0].url) == "http://printer.local/api/version"
        assert seen[0].headers["X-Api-Key"] == API_KEY

    def test_strips_a_trailing_slash_from_the_configured_url(self) -> None:
        client = OctoPrintClient("http://printer.local/", api_key=API_KEY)

        # Stored hosts routinely carry the slash a browser adds; keeping it would
        # produce `//api/version` on every request.
        assert client.base_url == "http://printer.local"

    def test_re_exports_the_error_class_core_actually_raises(self) -> None:
        # Call sites `except OctoPrintError`. An alias that drifted from core's
        # class would turn every provider failure into an unhandled 500.
        assert OctoPrintError is CoreOctoPrintError
