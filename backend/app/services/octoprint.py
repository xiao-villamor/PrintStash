"""Backward-compatible application facade for the core OctoPrint client."""

from __future__ import annotations

import httpx
from printstash_core.printers.models import OctoPrintConfig
from printstash_core.printers.octoprint import (
    OctoPrintClient as _CoreOctoPrintClient,
)
from printstash_core.printers.octoprint import OctoPrintError as OctoPrintError


class OctoPrintClient(_CoreOctoPrintClient):
    """Legacy constructor over the framework-neutral core implementation."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            OctoPrintConfig(base_url, api_key),
            timeout=timeout,
            transport=transport,
        )
