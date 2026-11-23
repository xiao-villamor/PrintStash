"""Framework-neutral Moonraker HTTP and WebSocket client."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, AsyncContextManager, Protocol, cast
from urllib.parse import quote
from weakref import WeakValueDictionary

import httpx
import websockets
from websockets.exceptions import WebSocketException

from .contracts import PrinterClient, SnapshotCallback
from .models import (
    Capability,
    MachineControlConflict,
    MachineControlError,
    MoonrakerConfig,
    MotionAxis,
    PrintArtifactFormat,
    PrinterConfig,
    PrinterSnapshot,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
    TuningControl,
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
    "extruder": ["temperature", "target", "can_extrude"],
    "toolhead": ["position", "homed_axes", "axis_minimum", "axis_maximum", "extruder"],
    "gcode_move": [
        "gcode_position",
        "speed_factor",
        "extrude_factor",
        "absolute_coordinates",
        "absolute_extrude",
    ],
    "fan": ["speed"],  # Optional Klipper object; absent hardware returns no speed.
    "idle_timeout": ["state"],
    "webhooks": ["state", "state_message"],
}

_BED_MEASUREMENT_OBJECTS = {
    "bed_mesh": ["probed_matrix"],
    "probe": None,
    "configfile": ["settings"],
    "gcode": ["commands"],
    "manual_probe": ["is_active"],
}
_MEASURE_BED_COMMAND = "BED_MESH_CALIBRATE"
_BED_MEASUREMENT_TIMEOUT = 105.0  # Probe grids commonly outlast an ordinary request.
_BED_MEASUREMENT_READ_TIMEOUT = 5.0
_IDLE_PRINT_STATES = frozenset({"standby", "complete", "cancelled"})
_LIVE_PRINT_STATES = _IDLE_PRINT_STATES | {"printing", "paused"}

# API, dispatch and provider clients are separate objects. Serialize commands
# for the same Moonraker controller within this process, without retaining
# locks for removed printers. Emergency stop always bypasses this queue.
_COMMAND_LOCKS: WeakValueDictionary[
    tuple[asyncio.AbstractEventLoop, str], asyncio.Lock
] = WeakValueDictionary()


def _coordinate(position: tuple[int | float, ...] | None, index: int) -> int | float:
    if position is None or len(position) <= index:
        raise MachineControlError(MachineControlConflict.UNAVAILABLE)
    return position[index]


def _gcode_decimal(value: float) -> str:
    # Scientific notation would introduce an E parameter in a traditional G-code.
    return f"{value:.9f}".rstrip("0").rstrip(".")


MOONRAKER_CAPABILITIES = ProviderCapabilities(
    supported=frozenset(Capability),
    accepted_print_formats=frozenset({PrintArtifactFormat.GCODE_TEXT}),
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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: float | None = None,
        require_gcode_receipt: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = await self._http_client.request(
                method,
                url,
                headers=self._headers(),
                timeout=self.timeout if timeout is None else timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise MoonrakerError(f"transport error: {exc}") from exc
        if not 200 <= response.status_code < 300:
            raise MoonrakerError(
                f"moonraker {response.status_code}: {response.text[:200]}"
            )
        try:
            payload: object = response.json()
        except ValueError:
            if require_gcode_receipt and response.text.strip().lower() != "ok":
                raise MoonrakerError("no G-code receipt") from None
            return {"raw": response.text}
        if not isinstance(payload, dict):
            raise MoonrakerError("invalid Moonraker response: expected a JSON object")
        if require_gcode_receipt and (
            payload.get("result") != "ok" or "error" in payload
        ):
            raise MoonrakerError("no G-code receipt")
        return payload

    async def info(self) -> dict[str, Any]:
        return await self._request("GET", "/printer/info")

    async def server_info(self) -> dict[str, Any]:
        return await self._request("GET", "/server/info")

    async def server_config(self) -> dict[str, Any]:
        return await self._request("GET", "/server/config")

    async def _query_objects(
        self, objects: dict[str, list[str] | None]
    ) -> dict[str, Any]:
        params = "&".join(
            (f"{name}={','.join(fields)}" if fields else name)
            for name, fields in objects.items()
        )
        return await self._request("GET", f"/printer/objects/query?{params}")

    async def query_status(self) -> dict[str, Any]:
        status = await self._query_objects(SUBSCRIPTIONS)
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
        except TypeError, ValueError:
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
        async with self._command_lock():
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
        async with self._command_lock():
            return await self._request("POST", "/printer/print/resume")

    async def resume(self) -> dict[str, Any]:
        return await self.resume_print()

    async def cancel_print(self) -> dict[str, Any]:
        return await self._request("POST", "/printer/print/cancel")

    async def cancel(self) -> dict[str, Any]:
        return await self.cancel_print()

    async def run_gcode(self, script: str) -> dict[str, Any]:
        async with self._command_lock():
            return await self._send_gcode(script)

    async def _send_gcode(
        self, script: str, *, timeout: float | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/printer/gcode/script",
            params={"script": script},
            timeout=timeout,
            require_gcode_receipt=True,
        )

    def _command_lock(self) -> asyncio.Lock:
        key = (asyncio.get_running_loop(), self.base_url)
        lock = _COMMAND_LOCKS.get(key)
        if lock is None:
            lock = _COMMAND_LOCKS[key] = asyncio.Lock()
        return lock

    async def _machine_state(self, *, idle: bool) -> PrinterSnapshot:
        return self._validate_machine_state(
            await self._query_objects(SUBSCRIPTIONS), idle=idle
        )

    @staticmethod
    def _validate_machine_state(
        report: dict[str, Any], *, idle: bool
    ) -> PrinterSnapshot:
        result = report.get("result")
        if not isinstance(result, Mapping) or not isinstance(
            result.get("status"), Mapping
        ):
            raise MachineControlError(MachineControlConflict.UNAVAILABLE)
        snapshot = PrinterSnapshot.from_legacy_payload(report)
        if snapshot.webhook_state != "ready":
            raise MachineControlError(MachineControlConflict.NOT_READY)
        if snapshot.state not in _LIVE_PRINT_STATES:
            raise MachineControlError(MachineControlConflict.UNAVAILABLE)
        if idle and snapshot.state not in _IDLE_PRINT_STATES:
            raise MachineControlError(MachineControlConflict.BUSY)
        return snapshot

    @staticmethod
    def _require_idle_motion(snapshot: PrinterSnapshot) -> None:
        idle_timeout = snapshot.extra.get("idle_timeout")
        state = idle_timeout.get("state") if isinstance(idle_timeout, Mapping) else None
        if state == "Printing":
            raise MachineControlError(MachineControlConflict.BUSY)
        if state not in ("Idle", "Ready"):
            raise MachineControlError(MachineControlConflict.UNAVAILABLE)

    async def home(self, axes: tuple[MotionAxis, ...]) -> dict[str, Any]:
        async with self._command_lock():
            snapshot = await self._machine_state(idle=True)
            self._require_idle_motion(snapshot)
            script = " ".join(("G28", *(axis.value.upper() for axis in axes)))
            return await self._send_gcode(script)

    async def jog(self, axis: MotionAxis, distance: float) -> dict[str, Any]:
        if not 0.1 <= abs(distance) <= 10:
            raise ValueError(
                "Jog distance must be between 0.1 and 10 mm in either direction"
            )
        async with self._command_lock():
            snapshot = await self._machine_state(idle=True)
            if snapshot.homed_axes is None:
                raise MachineControlError(MachineControlConflict.UNAVAILABLE)
            if axis.value not in snapshot.homed_axes:
                raise MachineControlError(MachineControlConflict.AXIS_NOT_HOMED)
            self._check_jog_bounds(snapshot, axis, distance)
            move = snapshot.gcode_move
            if move.absolute_coordinates is None:
                raise MachineControlError(MachineControlConflict.UNAVAILABLE)
            return await self._linear_move(
                axis.value.upper(),
                axis.coordinate_index,
                distance,
                move.position,
                move.absolute_coordinates,
            )

    @staticmethod
    def _check_jog_bounds(
        snapshot: PrinterSnapshot, axis: MotionAxis, distance: float
    ) -> None:
        # Klipper checks its full kinematic envelope, including geometries
        # that cannot be described by three individual min/max ranges.
        if snapshot.axis_minimum is None and snapshot.axis_maximum is None:
            return
        index = axis.coordinate_index
        minimum = _coordinate(snapshot.axis_minimum, index)
        maximum = _coordinate(snapshot.axis_maximum, index)
        target = _coordinate(snapshot.position, index) + distance
        if not minimum <= target <= maximum:
            raise MachineControlError(MachineControlConflict.OUT_OF_BOUNDS)

    async def extrude(self, distance: float) -> dict[str, Any]:
        if not 0.1 <= abs(distance) <= 50:
            raise ValueError(
                "Extrusion distance must be between 0.1 and 50 mm in either direction"
            )
        async with self._command_lock():
            snapshot = await self._machine_state(idle=True)
            extruder = snapshot.extruder
            if (
                snapshot.active_extruder != "extruder"
                or extruder is None
                or extruder.can_extrude is None
            ):
                raise MachineControlError(MachineControlConflict.UNAVAILABLE)
            if not extruder.can_extrude:
                raise MachineControlError(MachineControlConflict.COLD_EXTRUDER)
            move = snapshot.gcode_move
            if (
                move.absolute_coordinates is None
                or move.absolute_extrude is None
                or move.extrude_factor is None
                or move.extrude_factor <= 0
            ):
                raise MachineControlError(MachineControlConflict.UNAVAILABLE)
            return await self._linear_move(
                "E",
                3,
                distance / move.extrude_factor,
                move.position,
                move.absolute_coordinates and move.absolute_extrude,
            )

    async def _linear_move(
        self,
        axis: str,
        index: int,
        distance: float,
        position: tuple[int | float, ...] | None,
        absolute: bool,
    ) -> dict[str, Any]:
        value = _coordinate(position, index) + distance if absolute else distance
        if not math.isfinite(value):
            raise MachineControlError(MachineControlConflict.UNAVAILABLE)
        # A single G1 avoids leaving G90/G91, M82/M83, feed rate, or overrides
        # changed if Klipper rejects the move or the response is lost.
        return await self._send_gcode(f"G1 {axis}{_gcode_decimal(value)}")

    async def tune(self, control: TuningControl, percent: float) -> dict[str, Any]:
        if control is TuningControl.FAN:
            if not 0 <= percent <= 100:
                raise ValueError("Fan percentage must be between 0 and 100")
        elif not 50 <= percent <= 150:
            raise ValueError("Print override percentage must be between 50 and 150")
        async with self._command_lock():
            snapshot = await self._machine_state(idle=False)
            if control is TuningControl.FAN:
                if snapshot.fan_speed is None or not 0 <= snapshot.fan_speed <= 1:
                    raise MachineControlError(MachineControlConflict.FAN_UNAVAILABLE)
                return await self._send_gcode(
                    f"M106 S{_gcode_decimal(percent * 255 / 100)}"
                )
            if control is TuningControl.SPEED:
                factor, command = snapshot.gcode_move.speed_factor, "M220"
            else:
                factor, command = snapshot.gcode_move.extrude_factor, "M221"
            if factor is None or factor <= 0:
                raise MachineControlError(MachineControlConflict.UNAVAILABLE)
            return await self._send_gcode(f"{command} S{_gcode_decimal(percent)}")

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
        reconnect: bool = True,
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
                if not reconnect:
                    raise MoonrakerError("moonraker websocket disconnected") from exc
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
        # The caller owns reconnects and invalidates its cached status on failure.
        await self.subscribe(on_status, stop_event=stop_event, reconnect=False)

    async def console_history(self) -> dict[str, Any]:
        return await self._request("GET", "/server/gcode_store", params={"count": 100})

    async def printer_objects(self) -> dict[str, Any]:
        return await self._request("GET", "/printer/objects/list")

    async def temperature_store(self) -> dict[str, Any]:
        return await self._request("GET", "/server/temperature_store")

    async def bed_mesh_status(self) -> dict[str, Any]:
        return await self._request("GET", "/printer/objects/query?bed_mesh")

    @staticmethod
    def _bed_measurement_supported(status: Mapping[str, Any]) -> bool:
        mesh, probe = status.get("bed_mesh"), status.get("probe")
        config, gcode = status.get("configfile"), status.get("gcode")
        settings = config.get("settings") if isinstance(config, Mapping) else None
        commands = gcode.get("commands") if isinstance(gcode, Mapping) else None
        # Klipper's automatic bed mesh uses the registered "probe" object.
        # BLTouch, eddy current, and other compatible plugins can publish it
        # even when their config section isn't named [probe].
        return (
            isinstance(mesh, Mapping)
            and "probed_matrix" in mesh
            and isinstance(probe, Mapping)
            and bool(probe)
            and isinstance(settings, Mapping)
            and "bed_mesh" in settings
            and not any(
                section.lower().split() == ["gcode_macro", "bed_mesh_calibrate"]
                for section in settings
            )
            and isinstance(commands, Mapping)
            and _MEASURE_BED_COMMAND in commands
        )

    async def bed_measurement_supported(self) -> bool:
        report = await self._query_objects(_BED_MEASUREMENT_OBJECTS)
        result = report.get("result")
        status = result.get("status") if isinstance(result, Mapping) else None
        if not isinstance(status, Mapping):
            raise MoonrakerError("invalid bed measurement status")
        return self._bed_measurement_supported(status)

    async def measure_bed(self) -> dict[str, Any]:
        lock = self._command_lock()
        if lock.locked():
            raise MachineControlError(MachineControlConflict.BUSY)
        async with lock:
            try:
                async with asyncio.timeout(_BED_MEASUREMENT_READ_TIMEOUT):
                    report = await self._query_objects(
                        {**SUBSCRIPTIONS, **_BED_MEASUREMENT_OBJECTS}
                    )
            except (MoonrakerError, TimeoutError) as exc:
                raise ProviderError("printer_offline", code="printer_offline") from exc
            snapshot = self._validate_machine_state(report, idle=True)
            self._require_idle_motion(snapshot)
            if not set("xyz").issubset(snapshot.homed_axes or ""):
                raise MachineControlError(MachineControlConflict.BED_AXES_NOT_HOMED)
            status = report["result"]["status"]
            if not self._bed_measurement_supported(status):
                raise MachineControlError(
                    MachineControlConflict.BED_MEASUREMENT_UNSUPPORTED
                )
            manual = status.get("manual_probe")
            if isinstance(manual, Mapping) and manual.get("is_active") is True:
                raise MachineControlError(MachineControlConflict.BUSY)

            try:
                async with asyncio.timeout(_BED_MEASUREMENT_TIMEOUT):
                    receipt = await self._send_gcode(
                        _MEASURE_BED_COMMAND, timeout=_BED_MEASUREMENT_TIMEOUT
                    )
                if receipt.get("result") != "ok":
                    raise MoonrakerError("no bed measurement receipt")
            except (MoonrakerError, TimeoutError) as exc:
                raise ProviderError(
                    "bed_measurement_unknown", code="bed_measurement_unknown"
                ) from exc

            try:
                async with asyncio.timeout(_BED_MEASUREMENT_READ_TIMEOUT):
                    return await self.bed_mesh_status()
            except (MoonrakerError, TimeoutError) as exc:
                raise ProviderError(
                    "bed_measurement_mesh_unavailable",
                    code="bed_measurement_mesh_unavailable",
                ) from exc

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
