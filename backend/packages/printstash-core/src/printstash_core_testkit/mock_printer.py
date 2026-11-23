# pyright: basic
"""A mock Moonraker + Spoolman service for printer contract tests.

Speaks the subset of Moonraker (HTTP + ``/websocket``) that ``MoonrakerClient``
drives, plus a minimal Spoolman (mounted at ``/spoolman``) so the consumption
write-back chain runs end to end — all without physical hardware.

The print is *simulated*: state is a pure function of elapsed wall-time scaled by
a speed factor (no background task, no lifespan hooks — so it runs unchanged under
the ``lifespan="off"`` ``start_server`` helper). Starting a print makes the WS push
a stream of ``printing`` ticks followed by one ``complete`` tick carrying
``filament_used``, which is exactly what ``PrinterHub`` needs to finish the job and
write usage back to Spoolman.

Run standalone::

    python -m tests.e2e.fakes.mock_printer --port 7125 --print-seconds 5

Register the printer with ``moonraker_url=http://HOST:PORT`` and (if testing
Spoolman) the Spoolman base URL ``http://HOST:PORT/spoolman``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Optional

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from .print_sim import PrintSim

# How often the WebSocket pushes a status update. Real Moonraker is faster; this
# is snappy enough for tests while keeping log noise down.
PUSH_INTERVAL_S = 0.5


class MockState:
    """In-memory printer + spool state with a wall-clock print simulation."""

    def __init__(
        self,
        *,
        total_mm: float,
        total_seconds: float,
        print_seconds: float,
        api_key: Optional[str] = None,
    ) -> None:
        self.sim = PrintSim(
            total_mm=total_mm, total_seconds=total_seconds, print_seconds=print_seconds
        )
        self.api_key = api_key

        self.files: list[dict] = [
            {"path": "existing.gcode", "size": 42, "modified": time.time()}
        ]
        self.file_contents: dict[str, bytes] = {}
        self.config_files: dict[str, bytes] = {
            "printer.cfg": b"[printer]\nkinematics: cartesian\n",
            "includes/disposable.cfg": b"# Disposable emulator configuration\r\n\r\n",
            "includes/read-only.conf": b"# Read-only emulator configuration\n",
        }
        self.config_read_only = False
        self.config_read_only_files = {"includes/read-only.conf"}
        self.config_save_unconfirmed = False
        self.history: list[dict] = []
        self._job_id = 0
        self.position = [0.0, 0.0, 0.0, 0.0]
        self.origin = [0.0, 0.0, 0.0, 0.0]
        self.axis_minimum = [0.0, 0.0, 0.0, 0.0]
        self.axis_maximum = [250.0, 250.0, 250.0, 0.0]
        self.homed_axes: set[str] = set()
        self.absolute_coordinates = True
        self.absolute_extrude = True
        self.speed_factor = 1.0
        self.extrude_factor = 1.0
        self.fan_speed: float | None = None
        self.has_fan = True
        self.heater_targets: dict[str, float] = {}
        self.active_extruder = "extruder"
        self.idle_state = "Idle"
        self.ready = True
        self.max_extrude_only_distance = 50.0

        filament = {
            "id": 1,
            "name": "Mock PLA Black",
            "material": "PLA",
            "density": 1.24,
            "diameter": 1.75,
            "price": 25.0,
            "spool_weight": 250,
            "vendor": {"id": 1, "name": "MockVendor"},
        }
        self.spools: dict[int, dict] = {
            1: {
                "id": 1,
                "registered": "2024-01-01T00:00:00Z",
                "remaining_weight": 1000.0,
                "used_weight": 0.0,
                "remaining_length": 330000.0,
                "archived": False,
                "filament": filament,
            }
        }

    # -- simulation ---------------------------------------------------------

    def start(self, filename: str) -> None:
        self.sim.start(filename)

    def pause(self) -> None:
        self.sim.pause()

    def resume(self) -> None:
        self.sim.resume()

    def cancel(self) -> None:
        self.sim.cancel()

    def emergency_stop(self) -> None:
        self.sim.emergency_stop()
        self.ready = False

    def run_gcode(self, script: str) -> None:
        """Apply the emulator's supported operators using Klipper units."""
        if not self.ready:
            raise ValueError("Printer is not ready")
        match script.split():
            case ["G28", *axes] if set(axes) <= set("XYZ"):
                for axis in axes or "XYZ":
                    index = "XYZ".index(axis)
                    self.homed_axes.add(axis.lower())
                    self.position[index] = self.axis_minimum[index]
                    self.origin[index] = 0.0
            case ["G1", target]:
                self._move(target)
            case ["M104", target]:
                self.heater_targets["extruder"] = self._gcode_value(target, "S")
            case ["M140", target]:
                self.heater_targets["heater_bed"] = self._gcode_value(target, "S")
            case ["M106", target] if self.has_fan:
                self.fan_speed = self._gcode_value(target, "S") / 255
            case ["M220", target]:
                self.speed_factor = self._gcode_value(target, "S") / 100
            case ["M221", target]:
                flow = self._gcode_value(target, "S") / 100
                e = (self.position[3] - self.origin[3]) / self.extrude_factor
                self.origin[3] = self.position[3] - e * flow
                self.extrude_factor = flow
            case _:
                raise ValueError("Unsupported G-code")

    @staticmethod
    def _gcode_value(parameter: str, name: str) -> float:
        if not parameter.startswith(name):
            raise ValueError("Unexpected G-code parameter")
        value = float(parameter[1:])
        if not math.isfinite(value):
            raise ValueError("Non-finite G-code parameter")
        return value

    def _move(self, target: str) -> None:
        axis = target[0]
        index = "XYZE".index(axis)
        value = self._gcode_value(target, axis)
        absolute = self.absolute_coordinates
        if axis == "E":
            absolute = absolute and self.absolute_extrude
            value *= self.extrude_factor
        new_position = value + (
            self.origin[index] if absolute else self.position[index]
        )
        if axis == "E":
            if not self.status()["extruder"]["can_extrude"]:
                raise ValueError("Extrude below minimum temp")
            if (
                abs(new_position - self.position[index])
                > self.max_extrude_only_distance
            ):
                raise ValueError("Extrude only move too long")
        else:
            if axis.lower() not in self.homed_axes:
                raise ValueError("Must home axis first")
            if not self.axis_minimum[index] <= new_position <= self.axis_maximum[index]:
                raise ValueError("Move out of range")
        self.position[index] = new_position

    def _record_history(self) -> None:
        self._job_id += 1
        self.history.insert(
            0,
            {
                "job_id": str(self._job_id),
                "filename": self.sim.filename,
                "status": "completed",
                "filament_used": self.sim.total_mm,
                "print_duration": self.sim.total_seconds,
                "total_duration": self.sim.total_seconds,
                "end_time": time.time(),
            },
        )

    def status(
        self, objects: Mapping[str, list[str] | None] | None = None
    ) -> dict[str, Any]:
        """Current Moonraker ``printer.objects`` snapshot.

        Latches ``printing`` -> ``complete`` once progress reaches 1.0; this is
        the only place the terminal transition happens, so it fires from both the
        HTTP query and the WS push path.
        """
        was_printing = self.sim.state == "printing"
        progress = self.sim.progress()
        if was_printing and self.sim.state == "complete":
            self._record_history()
        elapsed = self.sim.elapsed()

        filament_used = self.sim.filament_used()
        active = self.sim.is_active()
        hotend_target = self.heater_targets.get("extruder", 210.0 if active else 0.0)
        bed_target = self.heater_targets.get("heater_bed", 60.0 if active else 0.0)
        hotend = max(hotend_target, 25.0)
        fan_speed = (
            self.fan_speed if self.fan_speed is not None else (0.6 if active else 0.0)
        )
        gcode_position = [
            position - origin
            for position, origin in zip(self.position, self.origin, strict=True)
        ]
        gcode_position[3] /= self.extrude_factor
        status = {
            "print_stats": {
                "state": self.sim.state,
                "filename": self.sim.filename,
                "print_duration": round(elapsed, 2),
                "total_duration": round(elapsed, 2),
                "filament_used": filament_used,
                "message": "",
            },
            "virtual_sdcard": {
                "progress": progress,
                "file_position": int(progress * 1000),
                "file_size": 1000,
            },
            "heater_bed": {
                "temperature": max(bed_target, 25.0),
                "target": bed_target,
            },
            "extruder": {
                "temperature": hotend,
                "target": hotend_target,
                "can_extrude": hotend >= 170,
            },
            **({"fan": {"speed": fan_speed}} if self.has_fan else {}),
            "toolhead": {
                "position": self.position.copy(),
                "homed_axes": "xyz" if active else "".join(sorted(self.homed_axes)),
                "axis_minimum": self.axis_minimum.copy(),
                "axis_maximum": self.axis_maximum.copy(),
                "extruder": self.active_extruder,
            },
            "gcode_move": {
                "gcode_position": gcode_position,
                "absolute_coordinates": self.absolute_coordinates,
                "absolute_extrude": self.absolute_extrude,
                "speed_factor": self.speed_factor,
                "extrude_factor": self.extrude_factor,
            },
            "idle_timeout": {"state": self.idle_state},
            "webhooks": {
                "state": "ready" if self.ready else "shutdown",
                "state_message": "Printer is ready" if self.ready else "Emergency stop",
            },
        }
        if objects is None:
            return status
        return {
            name: status[name]
            if fields is None
            else {field: status[name].get(field) for field in fields}
            for name, fields in objects.items()
            if name in status
        }


def create_app(
    *,
    total_mm: float = 1200.0,
    total_seconds: float = 600.0,
    print_seconds: float = 5.0,
    api_key: Optional[str] = None,
) -> tuple[FastAPI, MockState]:
    """Build the mock app and return it with its mutable state (for assertions)."""
    state = MockState(
        total_mm=total_mm,
        total_seconds=total_seconds,
        print_seconds=print_seconds,
        api_key=api_key,
    )
    app = FastAPI()

    @app.middleware("http")
    async def moonraker_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        if (
            state.api_key
            and not request.url.path.startswith("/spoolman")
            and request.headers.get("X-Api-Key") != state.api_key
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    def ok(result: Any) -> JSONResponse:
        return JSONResponse({"result": result})

    # -- Moonraker HTTP -----------------------------------------------------

    @app.get("/printer/info")
    async def printer_info() -> JSONResponse:
        return ok({"hostname": "mock-moonraker", "state": "ready", "klipper_path": "/"})

    @app.get("/server/info")
    async def server_info() -> JSONResponse:
        return ok({"klippy_connected": True, "klippy_state": "ready"})

    @app.get("/server/config")
    async def server_config() -> JSONResponse:
        return ok({"config": {}})

    @app.get("/printer/objects/query")
    async def query(request: Request) -> JSONResponse:
        params = request.query_params
        if "configfile" in params:
            return ok({"status": {"configfile": {"config": {}, "settings": {}}}})
        objects = {
            name: fields.split(",") if fields else None
            for name, fields in params.items()
        }
        return ok({"status": state.status(objects if params else None)})

    @app.get("/server/files/list")
    async def files_list(root: str = "gcodes") -> JSONResponse:
        if root == "config":
            return ok(
                [{"path": path, **config_entry(path)} for path in state.config_files]
            )
        return ok(state.files)

    def config_entry(path: str) -> dict:
        return {
            "size": len(state.config_files[path]),
            "modified": 1700000000.0,
            "permissions": "r"
            if (state.config_read_only or path in state.config_read_only_files)
            else "rw",
        }

    @app.get("/server/files/roots")
    async def file_roots() -> JSONResponse:
        return ok(
            [
                {"name": "gcodes", "permissions": "rw"},
                {
                    "name": "config",
                    "permissions": "r" if state.config_read_only else "rw",
                },
            ]
        )

    @app.get("/server/files/directory")
    async def directory(path: str) -> JSONResponse:
        parent = path.removeprefix("config").lstrip("/")
        return ok(
            {
                "files": [
                    {"filename": name.rsplit("/", 1)[-1], **config_entry(name)}
                    for name in state.config_files
                    if name.rpartition("/")[0] == parent
                ]
            }
        )

    @app.get("/server/files/config/{path:path}")
    async def config_download(path: str) -> Response:
        if path not in state.config_files:
            return Response(status_code=404)
        return Response(state.config_files[path], media_type="application/octet-stream")

    @app.delete("/server/files/gcodes/{remote:path}")
    async def delete_file(remote: str) -> JSONResponse:
        state.files = [f for f in state.files if f["path"] != remote]
        return ok({"item": {"path": remote, "root": "gcodes"}, "action": "delete_file"})

    @app.post("/server/files/upload")
    async def upload(request: Request) -> JSONResponse:
        form = await request.form()
        upload_file = form["file"]
        if form.get("root") == "config":
            if not isinstance(upload_file, UploadFile) or not upload_file.filename:
                return JSONResponse({"error": "invalid upload"}, status_code=400)
            path = "/".join(
                filter(None, (str(form.get("path", "")), upload_file.filename))
            )
            if path not in state.config_files:
                return JSONResponse(
                    {"error": "missing config fixture"}, status_code=404
                )
            if config_entry(path)["permissions"] != "rw":
                return JSONResponse({"error": "read-only file"}, status_code=403)
            content = await upload_file.read()
            if form.get("checksum") != sha256(content).hexdigest() or "print" in form:
                return JSONResponse({"error": "invalid config fields"}, status_code=422)
            state.config_files[path] = content
            if state.config_save_unconfirmed:
                return JSONResponse(
                    {"error": "simulated lost acknowledgement"}, status_code=500
                )
            return JSONResponse(
                {"result": {"item": {"root": "config", "path": path}}}, status_code=201
            )
        if isinstance(upload_file, UploadFile):
            name = upload_file.filename or "upload.gcode"
            content = await upload_file.read()
            size = len(content)
            state.file_contents[name] = content
        else:
            name = "upload.gcode"
            size = 0
        state.files = [f for f in state.files if f["path"] != name]
        state.files.append({"path": name, "size": size, "modified": time.time()})
        started = str(form.get("print", "")).lower() in ("true", "1", "yes")
        if started:
            state.start(name)
        return ok({"item": {"path": name, "root": "gcodes"}, "print_started": started})

    @app.post("/printer/print/start")
    async def print_start(filename: str = "") -> JSONResponse:
        state.start(filename)
        return ok("ok")

    @app.post("/printer/print/pause")
    async def print_pause() -> JSONResponse:
        state.pause()
        return ok("ok")

    @app.post("/printer/print/resume")
    async def print_resume() -> JSONResponse:
        state.resume()
        return ok("ok")

    @app.post("/printer/print/cancel")
    async def print_cancel() -> JSONResponse:
        state.cancel()
        return ok("ok")

    @app.post("/printer/emergency_stop")
    async def emergency_stop() -> JSONResponse:
        state.emergency_stop()
        return ok("ok")

    @app.post("/printer/gcode/script")
    async def gcode_script(script: str) -> JSONResponse:
        try:
            state.run_gcode(script)
        except ValueError as exc:
            return JSONResponse({"error": {"message": str(exc)}}, status_code=400)
        return ok("ok")

    @app.get("/server/history/list")
    async def history_list(limit: int = 50) -> JSONResponse:
        jobs = state.history[:limit]
        return ok({"count": len(state.history), "jobs": jobs})

    # -- Moonraker WebSocket ------------------------------------------------

    @app.websocket("/websocket")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()
        authenticated = state.api_key is None

        async def pusher(objects: dict[str, list[str] | None]) -> None:
            while True:
                await asyncio.sleep(PUSH_INTERVAL_S)
                try:
                    await ws.send_text(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "method": "notify_status_update",
                                "params": [state.status(objects), time.time()],
                            }
                        )
                    )
                except Exception:
                    return

        push_task: Optional[asyncio.Task] = None
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("method") == "server.connection.identify":
                    supplied_key = (msg.get("params") or {}).get("api_key")
                    if state.api_key and supplied_key != state.api_key:
                        await ws.send_text(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": msg.get("id"),
                                    "error": {"code": 401, "message": "unauthorized"},
                                }
                            )
                        )
                        continue
                    authenticated = True
                    await ws.send_text(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": msg.get("id"),
                                "result": {"connection_id": 1},
                            }
                        )
                    )
                    continue
                if msg.get("method") == "printer.objects.subscribe":
                    if not authenticated:
                        await ws.send_text(
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": msg.get("id"),
                                    "error": {"code": 401, "message": "unauthorized"},
                                }
                            )
                        )
                        continue
                    objects = msg["params"]["objects"]
                    await ws.send_text(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": msg.get("id"),
                                "result": {
                                    "status": state.status(objects),
                                    "eventtime": time.time(),
                                },
                            }
                        )
                    )
                    if push_task is None:
                        push_task = asyncio.create_task(pusher(objects))
        except WebSocketDisconnect:
            pass
        finally:
            if push_task is not None:
                push_task.cancel()

    # -- Spoolman (mounted at /spoolman -> client appends /api/v1) -----------

    spool_app = FastAPI()

    @spool_app.get("/api/v1/info")
    async def spool_info() -> dict:
        return {"version": "mock-spoolman-1.0"}

    @spool_app.get("/api/v1/vendor")
    async def spool_vendors() -> list:
        return [{"id": 1, "name": "MockVendor"}]

    @spool_app.get("/api/v1/filament")
    async def spool_filaments() -> list:
        return [s["filament"] for s in state.spools.values()]

    @spool_app.get("/api/v1/spool")
    async def spool_list(allow_archived: bool = False) -> list:
        return [
            s for s in state.spools.values() if allow_archived or not s.get("archived")
        ]

    @spool_app.get("/api/v1/spool/{spool_id}")
    async def spool_get(spool_id: int) -> Any:
        spool = state.spools.get(spool_id)
        if spool is None:
            return JSONResponse({"message": "not found"}, status_code=404)
        return spool

    @spool_app.put("/api/v1/spool/{spool_id}/use")
    async def spool_use(spool_id: int, body: dict) -> Any:
        spool = state.spools.get(spool_id)
        if spool is None:
            return JSONResponse({"message": "not found"}, status_code=404)
        used = float(body.get("use_weight", 0.0))
        spool["remaining_weight"] = round(spool["remaining_weight"] - used, 4)
        spool["used_weight"] = round(spool.get("used_weight", 0.0) + used, 4)
        return spool

    @spool_app.get("/api/v1/setting/active_spool")
    async def active_spool() -> dict:
        # None => no native Moonraker hook is decrementing; write-back proceeds.
        return {"value": None}

    app.mount("/spoolman", spool_app)
    return app, state


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock Moonraker + Spoolman service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7125)
    parser.add_argument("--total-mm", type=float, default=1200.0)
    parser.add_argument("--total-seconds", type=float, default=600.0)
    parser.add_argument("--print-seconds", type=float, default=5.0)
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    import uvicorn

    app, _ = create_app(
        total_mm=args.total_mm,
        total_seconds=args.total_seconds,
        print_seconds=args.print_seconds,
        api_key=args.api_key,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
