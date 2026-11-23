"""Use workbench diagnostics through the application and real provider transport."""

import pytest
from fastapi import FastAPI, Request

from tests.factories import build_printer
from tests.fakes.server import start_server


class TestPrinterInspection:
    @pytest.mark.asyncio
    async def test_read_only_workbench_inspects_current_klipper_state(
        self, api, superuser_headers, e2e_db
    ) -> None:
        controller = FastAPI()

        @controller.get("/server/temperature_store")
        def temperature_store():
            return {
                "result": {
                    "heater_bed": {"temperatures": [23, 25], "targets": [60, 60]}
                }
            }

        @controller.get("/printer/objects/query")
        def printer_objects(request: Request):
            assert list(request.query_params) == ["bed_mesh"]
            return {
                "result": {
                    "status": {
                        "bed_mesh": {
                            "profile_name": "first-layer",
                            "mesh_min": [10, 10],
                            "mesh_max": [190, 190],
                            "probed_matrix": [[0.25, 0], [0.125, -0.125]],
                        }
                    }
                }
            }

        running = start_server(controller)
        try:
            printer = build_printer(e2e_db, moonraker_url=running.base_url)
            url = f"/api/v1/printers/{printer.id}"

            mesh = await api.get(f"{url}/bed-mesh", headers=superuser_headers)
            history = await api.get(f"{url}/heater-history", headers=superuser_headers)

            assert mesh.status_code == 200, mesh.text
            assert mesh.json()["state"] == "active"
            assert mesh.json()["variation_mm"] == 0.375
            assert history.status_code == 200, history.text
            assert history.json()["heater_bed"][-1] == {
                "age_ms": 0,
                "temperature": 25,
                "target": 60,
            }
        finally:
            running.stop()

    @pytest.mark.asyncio
    async def test_operator_measures_active_bed_mesh(
        self, api, superuser_headers, e2e_db
    ) -> None:
        controller = FastAPI()
        commands: list[str] = []
        new_grid = [[0.25, 0], [0.125, -0.125]]

        @controller.get("/printer/objects/query")
        def printer_objects(request: Request):
            status = {
                "bed_mesh": {
                    "mesh_min": [10, 10],
                    "mesh_max": [190, 190],
                    "profile_name": "default",
                    "probed_matrix": new_grid if commands else [[]],
                },
                "print_stats": {"state": "standby"},
                "webhooks": {"state": "ready"},
                "idle_timeout": {"state": "Ready"},
                "toolhead": {"homed_axes": "xyz"},
                # The eddy section publishes its installed device under the shared
                # runtime "probe" object.
                "probe": {"name": "probe_eddy_current bed_sensor", "last_query": False},
                "manual_probe": {"is_active": False},
                "configfile": {
                    "settings": {"bed_mesh": {}, "probe_eddy_current bed_sensor": {}}
                },
                "gcode": {"commands": {"BED_MESH_CALIBRATE": {}}},
            }
            return {
                "result": {
                    "status": {
                        key: status[key]
                        for key in request.query_params
                        if key in status
                    }
                }
            }

        @controller.post("/printer/gcode/script")
        def run_gcode(script: str):
            commands.append(script)
            return {"result": "ok"}

        running = start_server(controller)
        try:
            printer = build_printer(e2e_db, moonraker_url=running.base_url)
            url = f"/api/v1/printers/{printer.id}/bed-mesh"
            before = await api.get(url, headers=superuser_headers)
            assert before.json() == {"state": "inactive"}

            measured = await api.post(
                f"{url}/measurement",
                headers=superuser_headers,
                json={"confirmed": True},
            )

            assert measured.status_code == 200, measured.text
            assert measured.json()["state"] == "active"
            assert measured.json()["probed_matrix"] == new_grid
            assert commands == ["BED_MESH_CALIBRATE"]
        finally:
            running.stop()
