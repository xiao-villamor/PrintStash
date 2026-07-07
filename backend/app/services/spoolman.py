"""Spoolman REST client.

Thin wrapper around the subset of the [Spoolman](https://github.com/Donkie/Spoolman)
REST API that PrintStash needs. Spoolman is the source of truth for spools,
filaments, and vendors; PrintStash *reads* inventory for display and *writes*
measured consumption back, never reimplementing the inventory itself.

The integration is optional and OFF by default (``SystemConfig.spoolman_enabled``):
every entry point is gated by the master switch, and all network failures are
translated to :class:`SpoolmanError` so a Spoolman outage degrades gracefully and
never blocks a print.

Reference: https://github.com/Donkie/Spoolman/blob/master/docs/openapi.json
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from sqlmodel import Session

from app.core.http_client import get_http_client
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["SpoolmanError", "SpoolmanClient", "get_spoolman_client"]

# Default per-request timeout. Kept short so a slow/dead Spoolman never stalls a
# UI request or a print-completion handler for long.
DEFAULT_TIMEOUT_S = 10.0


class SpoolmanError(RuntimeError):
    """Raised when a Spoolman request fails.

    ``code`` mirrors the :class:`MoonrakerError`/``ProviderError`` style so
    callers can branch on a stable string ("not_configured", "transport",
    "http", "unreachable") instead of parsing the message.
    """

    def __init__(self, message: str, *, code: str = "spoolman_error") -> None:
        super().__init__(message)
        self.code = code


class SpoolmanClient:
    """Read/write client for a single Spoolman instance.

    Uses the process-wide pooled ``httpx.AsyncClient``. Auth is optional —
    Spoolman can run unauthenticated on a homelab LAN, or behind a reverse proxy
    that expects an API key / bearer token.
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self.api_key:
            # Spoolman itself is keyless; a fronting proxy may expect a bearer
            # token. Send both common header shapes so either works.
            h["Authorization"] = f"Bearer {self.api_key}"
            h["X-Api-Key"] = self.api_key
        return h

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/api/v1{path}"
        client = get_http_client()
        try:
            resp = await client.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except httpx.HTTPError as exc:
            # httpx connect errors often stringify to "" — fall back to the
            # exception type so the UI shows something actionable, not a bare colon.
            detail = str(exc).strip() or exc.__class__.__name__
            raise SpoolmanError(f"transport error: {detail}", code="transport") from exc
        if resp.status_code < 200 or resp.status_code >= 300:
            raise SpoolmanError(
                f"spoolman {resp.status_code}: {resp.text[:200]}", code="http"
            )
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    # --- Read side ---------------------------------------------------------

    async def health_check(self) -> Dict[str, Any]:
        """Cheap reachability + version probe (Spoolman ``/info``)."""
        data = await self._request("GET", "/info")
        return data if isinstance(data, dict) else {"raw": data}

    async def list_vendors(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/vendor")
        return data if isinstance(data, list) else []

    async def list_filaments(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/filament")
        return data if isinstance(data, list) else []

    async def list_spools(
        self, *, include_archived: bool = False
    ) -> List[Dict[str, Any]]:
        params = {"allow_archived": "true" if include_archived else "false"}
        data = await self._request("GET", "/spool", params=params)
        return data if isinstance(data, list) else []

    async def get_spool(self, spool_id: int) -> Dict[str, Any]:
        data = await self._request("GET", f"/spool/{spool_id}")
        return data if isinstance(data, dict) else {"raw": data}

    # --- Write side --------------------------------------------------------

    async def use_spool_weight(self, spool_id: int, used_g: float) -> Dict[str, Any]:
        """Decrement a spool's remaining weight by ``used_g`` grams.

        Spoolman performs the subtraction server-side (``PUT /spool/{id}/use``),
        so it stays the source of truth for remaining weight.
        """
        data = await self._request(
            "PUT", f"/spool/{spool_id}/use", json={"use_weight": used_g}
        )
        return data if isinstance(data, dict) else {"raw": data}

    # --- Double-count detection -------------------------------------------

    async def active_spool(self) -> Optional[int]:
        """Return the active spool id Moonraker's native Spoolman hook sets.

        Moonraker's built-in Spoolman integration tracks the currently loaded
        spool in Spoolman's ``extra`` settings under ``active_spool``. A non-null
        value means Moonraker is already decrementing that spool — our own
        write-back would double-count. Returns ``None`` when unset or unreadable.
        """
        try:
            data = await self._request("GET", "/setting/active_spool")
        except SpoolmanError:
            return None
        # Spoolman setting endpoints return {"value": "<json-encoded>"}.
        raw = data.get("value") if isinstance(data, dict) else None
        if raw in (None, "", "null"):
            return None
        try:
            return int(str(raw).strip().strip('"'))
        except (TypeError, ValueError):
            return None


def use_spool_weight_sync(
    base_url: str,
    api_key: Optional[str],
    spool_id: int,
    used_g: float,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> None:
    """Blocking variant of ``use_spool_weight`` for synchronous call sites.

    The live finish-tick handler runs in a worker thread (``asyncio.to_thread``)
    where the pooled async client isn't usable, so consumption write-back uses a
    short-lived blocking request. Raises :class:`SpoolmanError` on failure — the
    caller swallows it so a Spoolman outage never blocks a print.
    """
    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-Api-Key"] = api_key
    url = f"{base_url.rstrip('/')}/api/v1/spool/{spool_id}/use"
    try:
        resp = httpx.put(url, json={"use_weight": used_g}, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise SpoolmanError(f"transport error: {exc}", code="transport") from exc
    if resp.status_code < 200 or resp.status_code >= 300:
        raise SpoolmanError(
            f"spoolman {resp.status_code}: {resp.text[:200]}", code="http"
        )


def active_spool_sync(
    base_url: str,
    api_key: Optional[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Optional[int]:
    """Blocking variant of ``SpoolmanClient.active_spool`` for the finish-tick.

    Returns the active spool id Moonraker's native hook tracks, or ``None`` when
    unset/unreadable. Used by the consumption write-back to skip a decrement that
    Moonraker is already performing. Never raises — an unreadable setting is
    treated as "no native hook" so a Spoolman hiccup can't block the print path;
    the caller decides what a ``None`` means.
    """
    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-Api-Key"] = api_key
    url = f"{base_url.rstrip('/')}/api/v1/setting/active_spool"
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
        if resp.status_code < 200 or resp.status_code >= 300:
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    # Spoolman setting endpoints return {"value": "<json-encoded>"}.
    raw = data.get("value") if isinstance(data, dict) else None
    if raw in (None, "", "null"):
        return None
    try:
        return int(str(raw).strip().strip('"'))
    except (TypeError, ValueError):
        return None


def get_spoolman_client(session: Session) -> SpoolmanClient:
    """Build a client from persisted config, or raise ``not_configured``.

    Parallels ``printer_provider.get_provider_client``: callers gate on the
    master switch first, then construct here. Raises when no base URL is set so
    the master switch and connection config can never silently disagree.
    """
    # Imported here to avoid a circular import (runtime_config imports models
    # which import services indirectly during startup).
    from app.services import runtime_config

    config = runtime_config.spoolman_config(session)
    base_url = config.get("base_url")
    if not base_url:
        raise SpoolmanError("Spoolman base URL is not configured", code="not_configured")
    return SpoolmanClient(base_url, config.get("api_key"))
