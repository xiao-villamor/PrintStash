"""Centauri protocol fakes.

CC1 uses real SDCP v3 frames over loopback WebSocket, exercising pycentauri's
transport, parsing, command envelopes, and status normalization. CC2 remains a
connection-seam fake because its local protocol requires MQTT registration plus
an HTTP serial-number bootstrap.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Callable

from pycentauri import sdcp
from pycentauri.client import Printer
from pycentauri.models import PrintStatus, Status
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from app.services.elegoo_centauri import ElegooCentauriError

from .print_sim import PrintSim

# PrintSim.state -> SDCP PrintInfo.Status code (see pycentauri.models.PrintStatus).
_STATE_CODES = {
    "standby": int(PrintStatus.IDLE),
    "printing": int(PrintStatus.PRINTING),
    "paused": int(PrintStatus.PAUSED),
    "complete": int(PrintStatus.COMPLETED),
    "cancelled": int(PrintStatus.STOPPED),
    "error": int(PrintStatus.ERROR),
}

# How often watch() pushes a status tick.
PUSH_INTERVAL_S = 0.2
MAINBOARD_ID = "MOCK-CENTAURI-MAINBOARD"


def _status_payload(sim: PrintSim) -> dict[str, Any]:
    sim.progress()
    active = sim.is_active()
    return {
        "TempOfNozzle": 210.0 if active else 25.0,
        "TempTargetNozzle": 210.0 if active else 0.0,
        "TempOfHotbed": 60.0 if active else 25.0,
        "TempTargetHotbed": 60.0 if active else 0.0,
        "TempOfBox": 32.0 if active else 22.0,
        "PrintInfo": {
            "Status": _STATE_CODES.get(sim.state, int(PrintStatus.ERROR)),
            "Filename": sim.filename,
            "Progress": round(sim.progress() * 100),
            "CurrentTicks": sim.elapsed(),
        },
        "Message": sim.message,
    }


@dataclass
class RunningCentauriServer:
    port: int
    _stop: threading.Event
    _thread: threading.Thread

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)


def start_cc1_server(sim: PrintSim) -> RunningCentauriServer:
    """Run SDCP v3 over a real loopback WebSocket for CC1 tests."""
    ready = threading.Event()
    stop = threading.Event()
    bound_port: list[int] = []
    startup_error: list[BaseException] = []

    async def handler(ws) -> None:  # noqa: ANN001
        await ws.send(
            json.dumps(
                {
                    "Id": MAINBOARD_ID,
                    "Topic": f"sdcp/attributes/{MAINBOARD_ID}",
                    "Data": {
                        "MainboardID": MAINBOARD_ID,
                        "Attributes": {"MainboardID": MAINBOARD_ID},
                    },
                }
            )
        )
        async for raw in ws:
            message = json.loads(raw)
            data = message.get("Data", {})
            cmd = int(data.get("Cmd", -1))
            command_data = data.get("Data", {})
            if cmd == int(sdcp.Cmd.START_PRINT):
                sim.start(command_data["Filename"])
            elif cmd == int(sdcp.Cmd.PAUSE_PRINT):
                sim.pause()
            elif cmd == int(sdcp.Cmd.RESUME_PRINT):
                sim.resume()
            elif cmd == int(sdcp.Cmd.STOP_PRINT):
                sim.cancel()
            await ws.send(
                json.dumps(
                    {
                        "Id": MAINBOARD_ID,
                        "Topic": f"sdcp/response/{MAINBOARD_ID}",
                        "Data": {
                            "Cmd": cmd,
                            "RequestID": data.get("RequestID"),
                            "MainboardID": MAINBOARD_ID,
                            "Data": {"Ack": 0},
                        },
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "Id": MAINBOARD_ID,
                        "Topic": f"sdcp/status/{MAINBOARD_ID}",
                        "Data": {"MainboardID": MAINBOARD_ID, **_status_payload(sim)},
                    }
                )
            )

    async def run() -> None:
        try:
            async with serve(handler, "127.0.0.1", 0, compression=None) as server:
                sockets = server.sockets
                if not sockets:
                    raise RuntimeError("mock Centauri WebSocket has no bound socket")
                bound_port.append(int(sockets[0].getsockname()[1]))
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(0.05)
        except BaseException as exc:
            startup_error.append(exc)
            ready.set()

    thread = threading.Thread(target=lambda: asyncio.run(run()), daemon=True)
    thread.start()
    if not ready.wait(10):
        stop.set()
        raise RuntimeError("mock Centauri WebSocket failed to start")
    if startup_error:
        raise RuntimeError(
            "mock Centauri WebSocket failed to start"
        ) from startup_error[0]
    return RunningCentauriServer(port=bound_port[0], _stop=stop, _thread=thread)


def cc1_connector(port: int) -> Callable[[bool], Any]:
    """Connect the real pycentauri client to a dynamically allocated fake port."""

    async def connector(enable_control: bool) -> Printer:
        printer = Printer("127.0.0.1", enable_control=enable_control)
        printer._ws = await connect(  # type: ignore[attr-defined]
            f"ws://127.0.0.1:{port}/websocket", max_size=None
        )
        printer._reader = asyncio.create_task(  # type: ignore[attr-defined]
            printer._read_loop(),  # type: ignore[attr-defined]
            name=f"pycentauri-reader-127.0.0.1:{port}",
        )
        return printer

    return connector


class FakeCentauriConnection:
    """Scriptable ``_CentauriConnection`` driven by a ``PrintSim``."""

    def __init__(self, sim: PrintSim) -> None:
        self.sim = sim
        self.closed = False
        self.calls: list[tuple[str, Any]] = []

    def _status(self) -> Status:
        return Status.from_payload(_status_payload(self.sim))

    async def status(self) -> Status:
        return self._status()

    async def watch(self) -> AsyncIterator[Status]:
        while True:
            yield self._status()
            await asyncio.sleep(PUSH_INTERVAL_S)

    async def start_print(self, filename: str, **kwargs: Any) -> Any:
        self.calls.append(("start_print", (filename, kwargs)))
        self.sim.start(filename)
        return {}

    async def pause(self) -> Any:
        self.calls.append(("pause", None))
        self.sim.pause()
        return {}

    async def resume(self) -> Any:
        self.calls.append(("resume", None))
        self.sim.resume()
        return {}

    async def stop(self) -> Any:
        self.calls.append(("stop", None))
        self.sim.cancel()
        return {}

    async def close(self) -> None:
        self.closed = True


def make_connector(
    sim: PrintSim,
    *,
    expected_access_code: str | None = None,
    given_access_code: str | None = None,
    fail_transport: bool = False,
    fail_after_connects: int | None = None,
) -> tuple[Callable[[bool], Any], FakeCentauriConnection]:
    """Build a ``Connector`` closure plus the connection it will hand back.

    Pass ``expected_access_code``/``given_access_code`` to simulate a Carbon 2
    access-code check (real firmware rejects the handshake with an
    "access code" error); ``fail_transport`` simulates a network-layer drop.
    """
    connection = FakeCentauriConnection(sim)
    connect_count = 0

    async def connector(enable_control: bool) -> FakeCentauriConnection:
        nonlocal connect_count
        # Mirrors ElegooCentauriClient._connect's own contract: the connector
        # is responsible for translating transport/auth failures into
        # ElegooCentauriError itself — `_with_connection`'s try/except only
        # wraps `action(connection)`, not the connect call.
        if fail_transport or (
            fail_after_connects is not None and connect_count >= fail_after_connects
        ):
            raise ElegooCentauriError("simulated connection refused")
        if (
            expected_access_code is not None
            and given_access_code != expected_access_code
        ):
            raise ElegooCentauriError(
                "access code rejected by printer", code="provider_authentication_failed"
            )
        connect_count += 1
        return connection

    return connector, connection
