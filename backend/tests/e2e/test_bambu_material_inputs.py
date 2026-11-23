"""Bambu MQTT material reports reach the permission-scoped printer material API."""

from __future__ import annotations

import pytest

from app.db.models import PrinterProvider
from app.modules.printing.printer_hub import PrinterHub
from app.modules.printing.printer_provider import BambuLanProvider
from tests.factories import build_printer
from tests.fakes.mock_bambu import make_mqtt_factory
from tests.fakes.print_sim import PrintSim


class TestExternalMaterialInputs:
    @pytest.mark.asyncio
    async def test_dual_external_spools_reach_material_state(
        self, api, superuser_headers, e2e_db
    ):
        serial = "01S00A000000000"
        printer = build_printer(
            e2e_db,
            provider=PrinterProvider.BAMBU_LAN,
            bambu_host="127.0.0.1",
            bambu_serial=serial,
            bambu_access_code="12345678",
        )
        factory, _ = make_mqtt_factory(
            PrintSim(total_mm=1000, total_seconds=10, print_seconds=1),
            pushall_report={
                "print": {
                    "vt_tray": {"tray_type": "PLA"},
                    "vir_slot": [
                        {"id": "255", "tray_type": "PLA"},
                        {"id": "254", "tray_type": "PETG"},
                    ],
                }
            },
        )
        provider = BambuLanProvider(
            host="127.0.0.1",
            serial=serial,
            access_code="12345678",
            mqtt_client_factory=factory,
        )
        report = await provider.query_status()
        await PrinterHub()._handle_status(printer.id, report["result"]["status"])
        response = await api.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=superuser_headers
        )
        assert response.status_code == 200
        feeds = {
            slot["slot_key"]: (slot["material_type"], slot["tool_key"])
            for slot in response.json()["slots"]
        }
        assert feeds == {"external": ("PLA", None), "external:254": ("PETG", None)}
