"""Framework-neutral Moonraker HTTP and WebSocket client."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, AsyncContextManager, Protocol, cast
from urllib.parse import quote

import httpx
import websockets
from websockets.exceptions import WebSocketException

from .contracts import PrinterClient, SnapshotCallback
from .models import (
    Capability,
    MoonrakerConfig,
    PrinterConfig,
    PrinterSnapshot,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
)


class MoonrakerError(RuntimeError):
    """Raised when a Moonraker request fails."""


SUBSCRIPTIONS: dict[str, list[str] | None] = {
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

MOONRAKER_CAPABILITIES = ProviderCapabilities(
    supported=frozenset(Capability),
    support_level="stable",
)


class _WebSocket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def ping(self) -> Any: ...


WebSocketConnector = Callable[..., AsyncContextManager[_WebSocket]]
HttpClientFactory = Callable[[], httpx.AsyncClient]


def _default_websocket_connector(
    url: str, **kwargs: Any
) -> AsyncContextManager[_WebSocket]:
    return cast(AsyncContextManager[_WebSocket], websockets.connect(url, **kwargs))


class MoonrakerClient:
    """Client for the Moonraker wire API using a typed core configuration."""

    capabilities = MOONRAKER_CAPABILITIES

    def __init__(
        self,
        config: MoonrakerConfig,
        *,
        timeout: float = 30.0,
        http_client_factory: HttpClientFactory | None = None,
        websocket_connector: WebSocketConnector | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key
        self.timeout = timeout
        self._http_client = (http_client_factory or httpx.AsyncClient)()
        self._websocket_connector = websocket_connector or _default_websocket_connector
        self._logger = logger or logging.getLogger(__name__)

    async def aclose(self) -> None:
        await self._http_client.aclose()

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"X-Api-Key": self.api_key}
        return {}

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = await self._http_client.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise MoonrakerError(f"transport error: {exc}") from exc
        if not 200 <= response.status_code < 300:
            raise MoonrakerError(
                f"moonraker {response.status_code}: {response.text[:200]}"
            )
        try:
            return cast(dict[str, Any], response.json())
        except ValueError:
            return {"raw": response.text}

    async def info(self) -> dict[str, Any]:
        return await self._request("GET", "/printer/info")

    async def server_info(self) -> dict[str, Any]:
        return await self._request("GET", "/server/info")

    async def server_config(self) -> dict[str, Any]:
        return await self._request("GET", "/server/config")

    async def query_status(self) -> dict[str, Any]:
        params = "&".join(
            (f"{name}={','.join(fields)}" if fields else name)
            for name, fields in SUBSCRIPTIONS.items()
        )
        status = await self._request("GET", f"/printer/objects/query?{params}")
        try:
            spool = await self._request("GET", "/server/spoolman/spool_id")
        except MoonrakerError:
            spool_id = None
        else:
            result = spool.get("result")
            spool_id = result.get("spool_id") if isinstance(result, Mapping) else result
        material_slots = self._spoolman_material_slots(spool_id)
        status_result = status.get("result")
        if isinstance(status_result, dict) and isinstance(
            status_result.get("status"), dict
        ):
            status_result["status"]["material_slots"] = material_slots
        return status

    @staticmethod
    def _spoolman_material_slots(spool_id: object) -> list[dict[str, Any]]:
        if isinstance(spool_id, bool):
            spool_id = None
        try:
            normalized = (
                int(spool_id) if isinstance(spool_id, (str, int, float)) else None
            )
        except (TypeError, ValueError):
            normalized = None
        return [
            {
                "slot_key": "tool0",
                "label": "Moonraker active spool",
                "tool_key": "tool0",
                "state": "loaded" if normalized is not None else "unknown",
                "external_spool_id": normalized,
            }
        ]

    async def query_snapshot(self) -> PrinterSnapshot:
        return PrinterSnapshot.from_legacy_payload(await self.query_status())

    async def query_configfile(self) -> dict[str, Any]:
        return await self._request("GET", "/printer/objects/query?configfile")

    async def printer_config(self) -> dict[str, Any]:
        return await self.query_configfile()

    async def list_gcode_files(self) -> dict[str, Any]:
        return await self._request("GET", "/server/files/list?root=gcodes")

    async def list_files(self) -> list[Mapping[str, Any]]:
        body = await self.list_gcode_files()
        result = body.get("result", [])
        return cast(list[Mapping[str, Any]], result) if isinstance(result, list) else []

    async def delete_gcode_file(self, remote_filename: str) -> dict[str, Any]:
        encoded = "/".join(quote(part, safe="") for part in remote_filename.split("/"))
        return await self._request(
            "DELETE",
            "/server/files/gcodes/" + encoded.lstrip("/"),
        )

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        return await self.delete_gcode_file(remote_filename)

    async def upload_gcode(
        self,
        local_path: Path,
        remote_filename: str,
        *,
        start_print: bool = False,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/server/files/upload"
        data = {"root": "gcodes", "print": "true" if start_print else "false"}
        with local_path.open("rb") as handle:
            files = {"file": (remote_filename, handle, "application/octet-stream")}
            try:
                response = await self._http_client.post(
                    url,
                    headers=self._headers(),
                    data=data,
                    files=files,
                    timeout=None,
                )
            except httpx.HTTPError as exc:
                raise MoonrakerError(f"upload transport error: {exc}") from exc
        if not 200 <= response.status_code < 300:
            raise MoonrakerError(
                f"upload failed {response.status_code}: {response.text[:200]}"
            )
        return cast(dict[str, Any], response.json())

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        return await self.upload_gcode(local_path, remote_filename, start_print=False)

    async def start_print(self, remote_filename: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/printer/print/start", params={"filename": remote_filename}
        )

    async def start(self, remote_filename: str) -> dict[str, Any]:
        return await self.start_print(remote_filename)

    async def pause_print(self) -> dict[str, Any]:
        return await self._request("POST", "/printer/print/pause")

    async def pause(self) -> dict[str, Any]:
        return await self.pause_print()

    async def resume_print(self) -> dict[str, Any]:
        return await self._request("POST", "/printer/print/resume")

    async def resume(self) -> dict[str, Any]:
        return await self.resume_print()

    async def cancel_print(self) -> dict[str, Any]:
        return await self._request("POST", "/printer/print/cancel")

    async def cancel(self) -> dict[str, Any]:
        return await self.cancel_print()

    async def run_gcode(self, script: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/printer/gcode/script", params={"script": script}
        )

    async def emergency_stop(self) -> dict[str, Any]:
        return await self._request("POST", "/printer/emergency_stop")

    async def get_print_history(self, limit: int = 50) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", "/server/history/list", params={"limit": limit}
        )
        jobs = data.get("result", {}).get("jobs", [])
        return cast(list[dict[str, Any]], jobs)

    def _ws_url(self) -> str:
        if self.base_url.startswith("https://"):
            return "wss://" + self.base_url[len("https://") :] + "/websocket"
        if self.base_url.startswith("http://"):
            return "ws://" + self.base_url[len("http://") :] + "/websocket"
        return self.base_url.rstrip("/") + "/websocket"

    async def subscribe(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Subscribe to flat status updates, preserving the legacy callback shape."""

        url = self._ws_url()
        backoff = 1.0
        request_id = 0

        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                self._logger.info("moonraker ws connect %s", url)
                async with self._websocket_connector(
                    url, max_size=8 * 1024 * 1024
                ) as websocket:
                    if self.api_key:
                        request_id += 1
                        identify_payload = {
                            "jsonrpc": "2.0",
                            "method": "server.connection.identify",
                            "params": {
                                "client_name": "PrintStash",
                                "version": "1",
                                "type": "agent",
                                "url": "https://printstash.io",
                                "api_key": self.api_key,
                            },
                            "id": request_id,
                        }
                        await websocket.send(json.dumps(identify_payload))
                        identify_raw = await asyncio.wait_for(
                            websocket.recv(), timeout=self.timeout
                        )
                        identify_response = cast(
                            dict[str, Any], json.loads(identify_raw)
                        )
                        if (
                            identify_response.get("id") != request_id
                            or "error" in identify_response
                        ):
                            error = identify_response.get("error", {})
                            detail = (
                                error.get("message", "authentication failed")
                                if isinstance(error, Mapping)
                                else "authentication failed"
                            )
                            raise MoonrakerError(
                                f"moonraker websocket authentication failed: {detail}"
                            )

                    request_id += 1
                    subscribe_payload = {
                        "jsonrpc": "2.0",
                        "method": "printer.objects.subscribe",
                        "params": {"objects": SUBSCRIPTIONS},
                        "id": request_id,
                    }
                    await websocket.send(json.dumps(subscribe_payload))
                    backoff = 1.0

                    while True:
                        if stop_event is not None and stop_event.is_set():
                            return
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=60.0)
                        except asyncio.TimeoutError:
                            await websocket.ping()
                            # Python 3.12 reimplemented `asyncio.wait_for` to
                            # await the coroutine directly instead of wrapping
                            # it in a task, so a `recv()` that finishes without
                            # suspending no longer yields to the event loop.
                            # Without this hop the receive/ping loop can spin
                            # and starve the rest of the process — including
                            # the stop event this loop is watching for.
                            await asyncio.sleep(0)
                            continue
                        try:
                            message = cast(dict[str, Any], json.loads(raw))
                        except ValueError:
                            continue

                        if message.get("id") == request_id and "result" in message:
                            result = message["result"]
                            status = (
                                result.get("status", {})
                                if isinstance(result, Mapping)
                                else {}
                            )
                            if status:
                                await on_status(dict(status))
                            continue

                        if message.get("method") == "notify_status_update":
                            params = message.get("params") or []
                            if isinstance(params, list) and params:
                                status = params[0]
                                if isinstance(status, Mapping):
                                    await on_status(dict(status))
                            continue
                        if message.get("method") == "notify_active_spool_set":
                            params = message.get("params") or []
                            spool_id = (
                                params[0]
                                if isinstance(params, list) and params
                                else None
                            )
                            await on_status(
                                {
                                    "material_slots": self._spoolman_material_slots(
                                        spool_id
                                    )
                                }
                            )
                            continue
            except (WebSocketException, OSError) as exc:
                self._logger.warning(
                    "moonraker ws error (%s); reconnect in %.1fs", exc, backoff
                )
                try:
                    await asyncio.wait_for(
                        stop_event.wait()
                        if stop_event is not None
                        else asyncio.sleep(backoff),
                        timeout=backoff,
                    )
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        await self.subscribe(on_status, stop_event=stop_event)

    async def subscribe_snapshots(
        self,
        on_snapshot: SnapshotCallback,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        async def convert(status: dict[str, Any]) -> None:
            await on_snapshot(PrinterSnapshot.from_legacy_payload(status))

        await self.subscribe(convert, stop_event=stop_event)


class MoonrakerFactory:
    provider_id = ProviderId.MOONRAKER
    capabilities = MOONRAKER_CAPABILITIES

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        http_client_factory: HttpClientFactory | None = None,
        websocket_connector: WebSocketConnector | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._timeout = timeout
        self._http_client_factory = http_client_factory
        self._websocket_connector = websocket_connector
        self._logger = logger

    def build(self, config: PrinterConfig) -> PrinterClient:
        if not isinstance(config, MoonrakerConfig):
            raise ProviderError(
                "provider_config_mismatch", code="provider_config_mismatch"
            )
        return MoonrakerClient(
            config,
            timeout=self._timeout,
            http_client_factory=self._http_client_factory,
            websocket_connector=self._websocket_connector,
            logger=self._logger,
        )
