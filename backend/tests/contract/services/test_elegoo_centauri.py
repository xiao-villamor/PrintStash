"""The Centauri CC1 client exchanges real SDCP frames over loopback WebSocket.

This contract fails when the provider and the printer emulator disagree about
command or status framing.
"""

from __future__ import annotations

import asyncio

from app.services.elegoo_centauri import ElegooCentauriClient
from app.services.printer_provider import ElegooCentauriProvider
from tests.fakes.mock_centauri import cc1_connector, start_cc1_server
from tests.fakes.print_sim import PrintSim

REMOTE = "demo.gcode"


async def _wait_state(
    provider: ElegooCentauriProvider, state: str, *, timeout: float = 10.0
) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await provider.query_status()
        if result["result"]["status"]["print_stats"]["state"] == state:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached state {state!r}")


def test_cc1_real_sdcp_websocket_round_trip() -> None:
    sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
    running = start_cc1_server(sim)
    provider = ElegooCentauriProvider(
        ElegooCentauriClient(
            "127.0.0.1",
            model="elegoo_centauri_carbon",
            connector=cc1_connector(running.port),
        )
    )
    try:

        async def _run() -> None:
            await provider.start(REMOTE)
            await _wait_state(provider, "printing")
            await provider.pause()
            await _wait_state(provider, "paused")
            await provider.resume()
            await _wait_state(provider, "printing")
            await provider.cancel()
            await _wait_state(provider, "cancelled")

        asyncio.run(_run())
    finally:
        running.stop()
