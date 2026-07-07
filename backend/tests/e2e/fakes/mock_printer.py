"""A mock Moonraker + Spoolman service for testing the printer pipeline.

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
import time
from typing import Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

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
        self.total_mm = total_mm
        self.total_seconds = total_seconds  # reported print_duration estimate
        # Sim "printer seconds" advance this many times faster than wall time so a
        # print reporting ``total_seconds`` completes in ``print_seconds`` real time.
        self.speed = total_seconds / max(print_seconds, 1e-3)
        self.api_key = api_key

        self.print_state = "standby"
        self.filename = ""
        self._started: Optional[float] = None  # monotonic when last (re)started
        self._accumulated = 0.0  # sim-seconds accrued before a pause

        self.files: list[dict] = [
            {"path": "existing.gcode", "size": 42, "modified": time.time()}
        ]
        self.history: list[dict] = []
        self._job_id = 0

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

    def _sim_elapsed(self) -> float:
        if self.print_state == "printing" and self._started is not None:
            return self._accumulated + (time.monotonic() - self._started) * self.speed
        return self._accumulated

    def start(self, filename: str) -> None:
        self.filename = filename
        self.print_state = "printing"
        self._started = time.monotonic()
        self._accumulated = 0.0

    def pause(self) -> None:
        if self.print_state == "printing":
            self._accumulated = self._sim_elapsed()
            self._started = None
            self.print_state = "paused"

    def resume(self) -> None:
        if self.print_state == "paused":
            self._started = time.monotonic()
            self.print_state = "printing"

    def cancel(self) -> None:
        self._accumulated = self._sim_elapsed()
        self._started = None
        self.print_state = "cancelled"

    def _record_history(self) -> None:
        self._job_id += 1
        self.history.insert(
            0,
            {
                "job_id": str(self._job_id),
                "filename": self.filename,
                "status": "completed",
                "filament_used": self.total_mm,
                "print_duration": self.total_seconds,
                "total_duration": self.total_seconds,
                "end_time": time.time(),
            },
        )

    def status(self) -> dict[str, Any]:
        """Current Moonraker ``printer.objects`` snapshot.

        Latches ``printing`` -> ``complete`` once progress reaches 1.0; this is
        the only place the terminal transition happens, so it fires from both the
        HTTP query and the WS push path.
        """
        elapsed = self._sim_elapsed()
        progress = min(elapsed / self.total_seconds, 1.0) if self.total_seconds else 0.0
        if self.print_state == "printing" and progress >= 1.0:
            self.print_state = "complete"
            self._accumulated = self.total_seconds
            self._started = None
            self._record_history()
        if self.print_state == "complete":
            progress, elapsed = 1.0, self.total_seconds

        filament_used = round(self.total_mm * progress, 4)
        active = self.print_state in ("printing", "paused")
        return {
            "print_stats": {
                "state": self.print_state,
                "filename": self.filename,
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
                "temperature": 60.0 if active else 25.0,
                "target": 60.0 if active else 0.0,
            },
            "extruder": {
                "temperature": 210.0 if active else 25.0,
                "target": 210.0 if active else 0.0,
            },
            "toolhead": {
                "position": [0.0, 0.0, 0.0, 0.0],
                "homed_axes": "xyz" if active else "",
            },
            "webhooks": {"state": "ready", "state_message": "Printer is ready"},
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
        snap = state.status()
        requested = [name for name in params.keys()] or list(snap.keys())
        return ok({"status": {name: snap[name] for name in requested if name in snap}})

    @app.get("/server/files/list")
    async def files_list() -> JSONResponse:
        return ok(state.files)

    @app.delete("/server/files/gcodes/{remote:path}")
    async def delete_file(remote: str) -> JSONResponse:
        state.files = [f for f in state.files if f["path"] != remote]
        return ok({"item": {"path": remote, "root": "gcodes"}, "action": "delete_file"})

    @app.post("/server/files/upload")
    async def upload(request: Request) -> JSONResponse:
        form = await request.form()
        upload_file = form["file"]
        name = getattr(upload_file, "filename", "upload.gcode")
        size = len(await upload_file.read()) if hasattr(upload_file, "read") else 0
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

    @app.get("/server/history/list")
    async def history_list(limit: int = 50) -> JSONResponse:
        jobs = state.history[:limit]
        return ok({"count": len(state.history), "jobs": jobs})

    # -- Moonraker WebSocket ------------------------------------------------

    @app.websocket("/websocket")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()

        async def pusher() -> None:
            while True:
                await asyncio.sleep(PUSH_INTERVAL_S)
                try:
                    await ws.send_text(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "method": "notify_status_update",
                                "params": [state.status(), time.time()],
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
                if msg.get("method") == "printer.objects.subscribe":
                    await ws.send_text(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": msg.get("id"),
                                "result": {
                                    "status": state.status(),
                                    "eventtime": time.time(),
                                },
                            }
                        )
                    )
                    if push_task is None:
                        push_task = asyncio.create_task(pusher())
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
            s
            for s in state.spools.values()
            if allow_archived or not s.get("archived")
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
