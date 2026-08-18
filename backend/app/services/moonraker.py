"""Backward-compatible application facade for the core Moonraker client."""

from __future__ import annotations

from typing import Any, Optional, cast

import httpx
from printstash_core.printers.models import MoonrakerConfig
from printstash_core.printers.moonraker import SUBSCRIPTIONS as SUBSCRIPTIONS
from printstash_core.printers.moonraker import (
    HttpClientFactory,
)
from printstash_core.printers.moonraker import (
    MoonrakerClient as _CoreMoonrakerClient,
)
from printstash_core.printers.moonraker import MoonrakerError as MoonrakerError

from app.core.http_client import close_http_client, get_http_client
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["get_http_client", "close_http_client", "MoonrakerError", "MoonrakerClient"]


class _DynamicHttpClient:
    """Resolve the application pool lazily to preserve its monkeypatch seam."""

    async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        return await get_http_client().request(*args, **kwargs)

    async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
        return await get_http_client().post(*args, **kwargs)

    async def aclose(self) -> None:
        # The application owns and closes its process-wide pooled client.
        return None


def _dynamic_http_client_factory() -> httpx.AsyncClient:
    return cast(httpx.AsyncClient, _DynamicHttpClient())


class MoonrakerClient(_CoreMoonrakerClient):
    """Legacy constructor over the framework-neutral core implementation."""

    def __init__(
        self, base_url: str, api_key: Optional[str] = None, *, timeout: float = 30.0
    ) -> None:
        super().__init__(
            MoonrakerConfig(base_url, api_key),
            timeout=timeout,
            http_client_factory=cast(
                HttpClientFactory, _dynamic_http_client_factory
            ),
            logger=logger,
        )
