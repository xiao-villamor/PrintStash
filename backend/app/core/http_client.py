"""Process-wide pooled ``httpx.AsyncClient``.

A single connection pool shared across all outbound HTTP (Moonraker calls,
URL imports). Clients are otherwise created per request/worker loop, so a
per-instance client would open a fresh TCP connection every call; pooling at
module level keeps connections alive across printers and requests.
"""

from __future__ import annotations

import httpx

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
        )
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None
