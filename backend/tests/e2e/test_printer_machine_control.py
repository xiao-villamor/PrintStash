"""The operator can control a registered printer through the real HTTP API."""

from __future__ import annotations

import asyncio

import pytest

from tests.factories import build_printer
from tests.fakes.mock_printer import create_app
from tests.fakes.server import start_server


class TestPrinterMachineControl:
    @pytest.mark.asyncio
    async def test_operator_moves_the_printer_via_typed_controls(
        self, api, superuser_headers, e2e_db
    ):
        app, state = create_app()
        running = start_server(app)
        try:
            printer = build_printer(
                e2e_db, name="Workbench", moonraker_url=running.base_url
            )
            path = f"/api/v1/printers/{printer.id}"

            async def control(action, payload):
                response = await api.post(
                    f"{path}/{action}", json=payload, headers=superuser_headers
                )
                assert response.status_code == 200, response.text
                assert response.json() == {"ok": True}

            await control("home", {"axes": "x"})
            # Each concurrent request must calculate its absolute target from the
            # preceding move, even though the product creates a provider per request.
            await asyncio.gather(
                control("jog", {"axis": "x", "distance": 1}),
                control("jog", {"axis": "x", "distance": 10}),
            )
            await control("temperature", {"heater": "extruder", "target": 210})
            await control("tune", {"control": "flow", "percent": 75})
            await control("extrude", {"distance": 5})
            await control("extrude", {"distance": -1})

            assert state.status()["toolhead"]["position"] == pytest.approx(
                [11, 0, 0, 4]
            )
        finally:
            running.stop()
