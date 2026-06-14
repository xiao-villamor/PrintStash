"""Moonraker HTTP + WebSocket client.

Tight, dependency-light wrapper around the subset of Moonraker we need:
- File upload to the printer's gcode store
- Start / pause / resume / cancel
- One-shot status query
- Persistent WS subscription with status callbacks

Reference: https://moonraker.readthedocs.io/en/latest/web_api/
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import quote

import httpx
import websockets

from app.core.http_client import close_http_client, get_http_client
from app.core.logging import get_logger

logger = get_logger(__name__)

# Re-exported from app.core.http_client for backward compatibility; the pooled
# client is shared across all outbound HTTP (Moonraker calls + URL imports).
__all__ = ["get_http_client", "close_http_client", "MoonrakerError", "MoonrakerClient"]


class MoonrakerError(RuntimeError):
    """Raised when a Moonraker request fails."""


# Objects and fields required for live printer state.
SUBSCRIPTIONS: Dict[str, Optional[list[str]]] = {
    "print_stats": [
        "state",
        "filename",
        "print_duration",
        "total_duration",
        "filament_used",
        "message",
    ],
    "virtual_sdcard": ["progress", "file_position", "file_size"],
    "heater_bed": ["temperature", "target"],
    "extruder": ["temperature", "target"],
    "toolhead": ["position", "homed_axes"],
    "webhooks": ["state", "state_message"],
}


class MoonrakerClient:
    def __init__(
        self, base_url: str, api_key: Optional[str] = None, *, timeout: float = 30.0
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        client = get_http_client()
        try:
            resp = await client.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except httpx.HTTPError as exc:
            raise MoonrakerError(f"transport error: {exc}") from exc
        if resp.status_code < 200 or resp.status_code >= 300:
            raise MoonrakerError(f"moonraker {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    async def info(self) -> Dict[str, Any]:
        return await self._request("GET", "/printer/info")

    async def server_info(self) -> Dict[str, Any]:
        return await self._request("GET", "/server/info")

    async def server_config(self) -> Dict[str, Any]:
        return await self._request("GET", "/server/config")

    async def query_status(self) -> Dict[str, Any]:
        params = "&".join(
            (f"{name}={','.join(fields)}" if fields else name)
            for name, fields in SUBSCRIPTIONS.items()
        )
        return await self._request("GET", f"/printer/objects/query?{params}")

    async def query_configfile(self) -> Dict[str, Any]:
        return await self._request("GET", "/printer/objects/query?configfile")

    async def list_gcode_files(self) -> Dict[str, Any]:
        return await self._request("GET", "/server/files/list?root=gcodes")

    async def delete_gcode_file(self, remote_filename: str) -> Dict[str, Any]:
        encoded = "/".join(quote(part, safe="") for part in remote_filename.split("/"))
        return await self._request(
            "DELETE",
            "/server/files/gcodes/" + encoded.lstrip("/"),
        )

    async def upload_gcode(
        self, local_path: Path, remote_filename: str, *, start_print: bool = False
    ) -> Dict[str, Any]:
        """Upload a g-code file to Moonraker. Streams from disk."""
        url = f"{self.base_url}/server/files/upload"
        data = {"root": "gcodes", "print": "true" if start_print else "false"}
        client = get_http_client()
        with local_path.open("rb") as fh:
            files = {"file": (remote_filename, fh, "application/octet-stream")}
            try:
                resp = await client.post(
                    url, headers=self._headers(), data=data, files=files, timeout=None
                )
            except httpx.HTTPError as exc:
                raise MoonrakerError(f"upload transport error: {exc}") from exc
        if resp.status_code < 200 or resp.status_code >= 300:
            raise MoonrakerError(f"upload failed {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def start_print(self, remote_filename: str) -> Dict[str, Any]:
        return await self._request(
            "POST", "/printer/print/start", params={"filename": remote_filename}
        )

    async def pause_print(self) -> Dict[str, Any]:
        return await self._request("POST", "/printer/print/pause")

    async def resume_print(self) -> Dict[str, Any]:
        return await self._request("POST", "/printer/print/resume")

    async def cancel_print(self) -> Dict[str, Any]:
        return await self._request("POST", "/printer/print/cancel")

    async def get_print_history(self, limit: int = 50) -> list[Dict[str, Any]]:
        data = await self._request(
            "GET", "/server/history/list", params={"limit": limit}
        )
        return data.get("result", {}).get("jobs", [])

    def _ws_url(self) -> str:
        if self.base_url.startswith("https://"):
            return "wss://" + self.base_url[len("https://") :] + "/websocket"
        if self.base_url.startswith("http://"):
            return "ws://" + self.base_url[len("http://") :] + "/websocket"
        return self.base_url.rstrip("/") + "/websocket"

    async def subscribe(
        self,
        on_status: Callable[[Dict[str, Any]], Awaitable[None]],
        *,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Open a WS, subscribe to printer objects, dispatch updates to `on_status`.

        Reconnects forever (or until stop_event is set) with exponential backoff.
        Each callback gets a flat dict of {object: {field: value}}.
        """
        url = self._ws_url()
        backoff = 1.0
        request_id = 0

        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                logger.info("moonraker ws connect %s", url)
                async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
                    request_id += 1
                    sub_payload = {
                        "jsonrpc": "2.0",
                        "method": "printer.objects.subscribe",
                        "params": {"objects": SUBSCRIPTIONS},
                        "id": request_id,
                    }
                    await ws.send(json.dumps(sub_payload))
                    backoff = 1.0

                    while True:
                        if stop_event is not None and stop_event.is_set():
                            return
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
                        except asyncio.TimeoutError:
                            await ws.ping()
                            continue
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue

                        if msg.get("id") == request_id and "result" in msg:
                            status = msg["result"].get("status", {})
                            if status:
                                await on_status(status)
                            continue

                        if msg.get("method") == "notify_status_update":
                            params = msg.get("params") or []
                            if params:
                                await on_status(params[0])
                            continue
            except (websockets.WebSocketException, OSError) as exc:
                logger.warning(
                    "moonraker ws error (%s); reconnect in %.1fs", exc, backoff
                )
                try:
                    await asyncio.wait_for(
                        (stop_event.wait() if stop_event else asyncio.sleep(backoff)),
                        timeout=backoff,
                    )
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)
