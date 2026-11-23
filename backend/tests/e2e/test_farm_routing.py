"""Mixed-farm dispatch through PrintStash with printer-side inventory and job readback."""

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from app.db.models import PrinterProvider, PrinterStatus
from app.main import app
from app.modules.printing.bambu_adapter import BambuLanProvider
from app.modules.printing.printer_jobs import dispatch_next
from app.modules.printing.printer_provider import get_provider_client
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import a_gcode_artifact, build_material_requirement, build_printer
from tests.fakes import mock_bambu
from tests.fakes.mock_printer import create_app
from tests.fakes.print_sim import PrintSim
from tests.fakes.server import start_server

QUEUE = "/api/v1/fleet/queue"
# Long enough to inspect the selected file while both native firmware simulators run.
GCODE = b";TIME:90\nG4 P30000\nM400\n"


def provider_for(printer):
    return get_provider_client(printer, registry=app.state.printer_provider_registry)


@pytest.fixture
def routing_devices(e2e_db, monkeypatch, request):
    controller, _ = create_app(print_seconds=getattr(request, "param", 90))
    server = start_server(controller)
    try:
        klipper = build_printer(
            e2e_db,
            group="manual feed",
            moonraker_url=server.base_url,
            provider_material_sync_enabled=False,
            operator_release_required=True,
            status=PrinterStatus.UNKNOWN,
        )
        bambu = build_printer(
            e2e_db,
            provider=PrinterProvider.BAMBU_LAN,
            group="multicolor",
            status=PrinterStatus.UNKNOWN,
        )
        # Only material fields from a disposable Farm Lab status report are replayed.
        # Print state, filename and progress continue to come from the running simulator.
        path = Path(__file__).parents[1] / "fixtures" / "bambu_routing_inventory.json"
        inventory = json.loads(path.read_text())
        live_report = mock_bambu._status_report

        def report(sim):
            packet = live_report(sim)
            packet["print"].update(inventory)
            return packet

        monkeypatch.setattr(mock_bambu, "_status_report", report)
        mqtt, _ = mock_bambu.make_mqtt_factory(
            PrintSim(total_mm=10, total_seconds=90, print_seconds=90)
        )
        ftp = mock_bambu.FakeFtpTls()
        monkeypatch.setattr(
            BambuLanProvider, "_default_mqtt_client", staticmethod(mqtt)
        )
        monkeypatch.setattr(BambuLanProvider, "_ftps_client", lambda self: ftp)
        yield klipper, bambu
    finally:
        server.stop()


class RoutingFarm:
    def __init__(self, api, session, printers):
        self.api = api
        self.session = session
        self.klipper, self.bambu = printers

    async def load_manual_feed(self, material):
        response = await self.api.put(
            f"/api/v1/printers/{self.klipper.id}/material-state/manual",
            json={
                "tools": [{"tool_key": "tool0", "label": "Extruder"}],
                "slots": [
                    {
                        "slot_key": "feed",
                        "label": "Spool",
                        "tool_key": "tool0",
                        "state": "loaded",
                        "material_type": material,
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text

    def revision(self, name, material, content=GCODE):
        file = a_gcode_artifact(self.session, name, dispatchable=True)
        build_material_requirement(
            self.session, file, material_type=material, color_hex=None
        )
        get_backend().write_bytes(content, file.path)
        return file

    async def enqueue(self, name, material, *, content=GCODE, **routing):
        file = self.revision(name, material, content)
        response = await self.api.post(
            QUEUE, json={"file_id": file.id, "strategy": "least_busy", **routing}
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def assert_delivery(self, job, destination, other):
        name = job["remote_filename"]
        device = provider_for(destination)
        inventory = {r["path"]: r["size"] for r in await device.list_files()}
        assert inventory[name] == len(GCODE)
        assert name not in {r["path"] for r in await provider_for(other).list_files()}
        status = (await device.query_status())["result"]["status"]["print_stats"]
        assert status["filename"] == name
        assert status["state"] == "printing"
        queue = await self.api.get(QUEUE)
        assert queue.status_code == 200
        row = next(r for r in queue.json() if r["id"] == job["id"])
        assert (row["state"], row["printer_id"], row["file_id"]) == (
            "started",
            destination.id,
            job["file_id"],
        )


@pytest_asyncio.fixture
async def farm(api, superuser_headers, e2e_db, routing_devices):
    api.headers.update(superuser_headers)
    for printer in routing_devices:
        report = await provider_for(printer).query_status()
        await app.state.printer_hub._handle_status(
            printer.id, report["result"]["status"]
        )
    farm = RoutingFarm(api, e2e_db, routing_devices)
    # Klipper's manual feed is maintained in PrintStash. Bambu's trays arrive from MQTT.
    await farm.load_manual_feed("PLA")
    return farm


@pytest.mark.asyncio
class TestFarmRouting:
    async def test_auto_routes_to_the_reported_petg_tray(self, farm):
        job = await farm.enqueue("PETG bracket", "PETG")
        assert job["printer_id"] == farm.bambu.id
        assert await dispatch_next(provider_for) == job["id"]
        await farm.assert_delivery(job, farm.bambu, farm.klipper)

    async def test_group_restriction_holds_until_the_requested_material_is_loaded(
        self, farm
    ):
        job = await farm.enqueue("Group bracket", "PETG", target_group="manual feed")
        assert job["blocked_reason"] == "no_material_compatible_printer"
        assert await dispatch_next(provider_for) is None
        for device in (farm.klipper, farm.bambu):
            assert job["remote_filename"] not in {
                r["path"] for r in await provider_for(device).list_files()
            }
        await farm.load_manual_feed("PETG")
        assert await dispatch_next(provider_for) == job["id"]
        await farm.assert_delivery(job, farm.klipper, farm.bambu)

    async def test_dispatch_reselects_after_the_assigned_printer_is_drained(self, farm):
        job = await farm.enqueue("PLA cover", "PLA")
        by_id = {p.id: p for p in (farm.klipper, farm.bambu)}
        original = by_id.pop(job["printer_id"])
        (other,) = by_id.values()
        response = await farm.api.patch(
            f"/api/v1/fleet/printers/{original.id}/routing", json={"drain_mode": True}
        )
        assert response.status_code == 200
        assert await dispatch_next(provider_for) == job["id"]
        await farm.assert_delivery(job, other, original)

    async def test_dispatched_job_rejects_an_older_route_edit(self, farm):
        job = await farm.enqueue(
            "Pinned cover", "PLA", strategy="manual", printer_id=farm.klipper.id
        )
        assert await dispatch_next(provider_for) == job["id"]
        edited = await farm.api.patch(
            f"{QUEUE}/{job['id']}",
            json={
                "strategy": "manual",
                "printer_id": farm.bambu.id,
                "expected_updated_at": job["updated_at"],
            },
        )
        assert edited.status_code == 409
        assert edited.json()["detail"] == "queue_job_not_editable"
        await farm.assert_delivery(job, farm.klipper, farm.bambu)

    @pytest.mark.parametrize("edit", ["rush", "move_to_front"])
    async def test_operator_order_determines_which_file_reaches_the_printer(
        self, farm, edit
    ):
        route = {"strategy": "manual", "printer_id": farm.klipper.id}
        first = await farm.enqueue("First plate", "PLA", **route)
        second = await farm.enqueue("Second plate", "PLA", **route)
        change = {"priority": "rush"} if edit == "rush" else {"queue_position": 1}
        edited = await farm.api.patch(f"{QUEUE}/{second['id']}", json=change)
        assert edited.status_code == 200
        assert await dispatch_next(provider_for) == second["id"]
        await farm.assert_delivery(second, farm.klipper, farm.bambu)
        assert await dispatch_next(provider_for) is None
        rows = (await farm.api.get(QUEUE)).json()
        pending = next(r for r in rows if r["id"] == first["id"])
        assert pending["state"] == "queued"
        assert first["remote_filename"] not in {
            r["path"] for r in await provider_for(farm.klipper).list_files()
        }

    async def test_blocked_rush_job_yields_to_runnable_work(self, farm):
        urgent = await farm.enqueue("ABS shell", "ABS", priority="rush")
        normal = await farm.enqueue("PETG insert", "PETG")
        assert await dispatch_next(provider_for) == normal["id"]
        await farm.assert_delivery(normal, farm.bambu, farm.klipper)
        rows = (await farm.api.get(QUEUE)).json()
        waiting = next(r for r in rows if r["id"] == urgent["id"])
        assert (waiting["state"], waiting["blocked_reason"]) == (
            "queued",
            "no_material_compatible_printer",
        )

    async def test_operator_moves_a_waiting_job_to_a_different_printer(self, farm):
        job = await farm.enqueue(
            "Rerouted plate", "PLA", strategy="manual", printer_id=farm.klipper.id
        )
        edit = await farm.api.patch(
            f"{QUEUE}/{job['id']}",
            json={
                "strategy": "manual",
                "printer_id": farm.bambu.id,
                "expected_updated_at": job["updated_at"],
            },
        )
        assert edit.status_code == 200, edit.text
        assert await dispatch_next(provider_for) == job["id"]
        await farm.assert_delivery(job, farm.bambu, farm.klipper)

    async def test_batch_over_capacity_runs_one_copy_on_each_printer(self, farm):
        revision = farm.revision("Three copies", "PLA")
        response = await farm.api.post(
            "/api/v1/fleet/batches",
            json={
                "file_id": revision.id,
                "strategy": "least_busy",
                "quantity": 3,
            },
        )
        assert response.status_code == 201, response.text
        copies = response.json()["jobs"]
        assert [copy["copy_index"] for copy in copies] == [1, 2, 3]
        for copy in copies[:2]:
            assert await dispatch_next(provider_for) == copy["id"]
        assert await dispatch_next(provider_for) is None
        queue = await farm.api.get(QUEUE)
        assert queue.status_code == 200
        rows = {row["id"]: row for row in queue.json()}
        active = [rows[copy["id"]] for copy in copies[:2]]
        assert {row["printer_id"] for row in active} == {farm.bambu.id, farm.klipper.id}
        assert all(row["state"] == "started" for row in active)
        assert rows[copies[2]["id"]]["state"] == "queued"
        for printer in (farm.klipper, farm.bambu):
            assigned = next(row for row in active if row["printer_id"] == printer.id)
            device = provider_for(printer)
            status = (await device.query_status())["result"]["status"]["print_stats"]
            assert (status["state"], status["filename"]) == (
                "printing",
                assigned["remote_filename"],
            )
            assert {row["path"]: row["size"] for row in await device.list_files()}[
                assigned["remote_filename"]
            ] == len(GCODE)

    async def test_device_local_print_removes_that_printer_from_routing(
        self, farm, tmp_path
    ):
        outside_print = tmp_path / "device-local.gcode"
        outside_print.write_bytes(GCODE)
        device = provider_for(farm.bambu)
        await device.upload(outside_print, outside_print.name)
        await device.start(outside_print.name)
        report = (await device.query_status())["result"]["status"]
        assert report["print_stats"]["filename"] == outside_print.name
        assert report["print_stats"]["state"] == "printing"
        await app.state.printer_hub._handle_status(farm.bambu.id, report)

        job = await farm.enqueue("PLA while Bambu busy", "PLA")
        assert job["printer_id"] == farm.klipper.id
        assert await dispatch_next(provider_for) == job["id"]
        await farm.assert_delivery(job, farm.klipper, farm.bambu)
        assert (await device.query_status())["result"]["status"]["print_stats"][
            "filename"
        ] == outside_print.name

    @pytest.mark.parametrize("routing_devices", [3], indirect=True, ids=["bed-release"])
    async def test_next_job_waits_for_bed_release_after_observed_completion(
        self, farm, routing_devices
    ):
        route = {"strategy": "manual", "printer_id": farm.klipper.id}
        first = await farm.enqueue(
            "Release coupon", "PLA", content=b"G4 P1000\nM400\n", **route
        )
        next_job = await farm.enqueue("Following plate", "PLA", **route)
        assert await dispatch_next(provider_for) == first["id"]
        device = provider_for(farm.klipper)
        async with asyncio.timeout(25):
            while True:
                status = (await device.query_status())["result"]["status"]
                await app.state.printer_hub._handle_status(farm.klipper.id, status)
                if status["print_stats"]["state"] == "complete":
                    break
                await asyncio.sleep(0.15)
        jobs = (await farm.api.get(QUEUE)).json()
        finished = next(row for row in jobs if row["id"] == first["id"])
        assert (finished["state"], finished["operator_gate_state"]) == (
            "completed",
            "pending",
        )
        assert await dispatch_next(provider_for) is None
        assert next_job["remote_filename"] not in {
            row["path"] for row in await device.list_files()
        }

        released = await farm.api.post(
            f"{QUEUE}/{first['id']}/operator-decision", json={"action": "release"}
        )
        assert released.status_code == 200
        assert released.json()["operator_gate_state"] == "released"
        assert await dispatch_next(provider_for) == next_job["id"]
        await farm.assert_delivery(next_job, farm.klipper, farm.bambu)
