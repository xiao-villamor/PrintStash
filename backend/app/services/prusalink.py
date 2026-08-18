"""Backward-compatible application facade for the core PrusaLink client."""

from __future__ import annotations

import httpx
from printstash_core.printers.models import PrusaLinkConfig
from printstash_core.printers.prusalink import (
    PrusaLinkClient as _CorePrusaLinkClient,
)
from printstash_core.printers.prusalink import PrusaLinkError as PrusaLinkError


class PrusaLinkClient(_CorePrusaLinkClient):
    """Legacy constructor over the framework-neutral core implementation."""

    def __init__(
        self,
        base_url: str,
        *,
        auth_mode: str,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            PrusaLinkConfig(
                base_url,
                auth_mode,
                username=username,
                password=password,
                api_key=api_key,
            ),
            timeout=timeout,
            transport=transport,
        )
