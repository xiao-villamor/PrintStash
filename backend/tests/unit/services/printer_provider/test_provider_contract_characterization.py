"""Characterization tests for the current printer-provider boundary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pycentauri.models import Status

from app.db.models import PrinterProvider
from app.services.elegoo_centauri import ElegooCentauriClient
from app.services.octoprint import OctoPrintClient
from app.services.printer_provider import (
    BambuLanProvider,
    Capability,
    ElegooCentauriProvider,
    MoonrakerProvider,
    OctoPrintProvider,
    PrusaLinkProvider,
    capabilities_for_provider,
)
from app.services.prusalink import PrusaLinkClient


@pytest.mark.asyncio
async def test_moonraker_status_contract_preserves_canonical_wire_shape() -> None:
    expected = {
        "result": {
            "status": {
                "print_stats": {
                    "state": "printing",
                    "filename": "cube.gcode",
                    "print_duration": 120.0,
                    "total_duration": 130.0,
                    "filament_used": 1234.5,
                    "message": "",
                },
                "virtual_sdcard": {
                    "progress": 0.25,
                    "file_position": 250,
                    "file_size": 1000,
                },
                "heater_bed": {"temperature": 59.5, "target": 60.0},
                "extruder": {"temperature": 214.0, "target": 215.0},
                "toolhead": {"position": [1.0, 2.0, 3.0, 4.0], "homed_axes": "xyz"},
                "webhooks": {"state": "ready", "state_message": "Printer is ready"},
            }
        }
    }
    provider = MoonrakerProvider("http://printer.invalid")

    with patch.object(
        provider.client, "_request", new_callable=AsyncMock, return_value=expected
    ):
        result = await provider.query_status()

    assert result == expected


@pytest.mark.asyncio
async def test_bambu_status_contract_is_sparse_and_preserves_external_metadata() -> (
    None
):
    report = {
        "print": {
            "gcode_state": "RUNNING",
            "mc_percent": 42,
            "gcode_file": "/cache/plate_1.gcode",
            "print_error": "",
            "subtask_name": "Benchy",
            "task_id": "task-42",
            "subtask_id": "subtask-7",
            "project_id": "project-3",
            "profile_id": "profile-2",
            "plate_num": 1,
            "layer_num": 8,
            "total_layer_num": 120,
            "nozzle_diameter": 0.4,
        }
    }
    provider = BambuLanProvider("192.0.2.10", "TEST-SERIAL", "test-code")

    with patch.object(provider, "_mqtt_request", return_value=report):
        result = await provider.query_status()

    assert result == {
        "result": {
            "status": {
                "print_stats": {
                    "state": "printing",
                    "filename": "plate_1.gcode",
                    "message": "",
                    "external_display_name": "Benchy",
                    "external_task_id": "task-42",
                    "external_subtask_id": "subtask-7",
                    "external_project_id": "project-3",
                    "external_profile_id": "profile-2",
                    "external_gcode_file": "/cache/plate_1.gcode",
                    "external_plate_index": 1,
                    "external_current_layer": 8,
                    "external_total_layers": 120,
                    "external_nozzle_diameter": 0.4,
                },
                "virtual_sdcard": {"progress": 0.42},
                "material_tools": [
                    {
                        "tool_key": "tool0",
                        "label": "Tool 0",
                        "nozzle_diameter_mm": 0.4,
                    }
                ],
            }
        }
    }


@pytest.mark.asyncio
async def test_prusalink_status_contract_uses_canonical_snapshot_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/status":
            return httpx.Response(
                200,
                json={
                    "printer": {
                        "state": "PRINTING",
                        "telemetry": {
                            "temp-bed": {"actual": 59.5, "target": 60},
                            "temp-nozzle": {"actual": 214, "target": 215},
                        },
                    },
                    "message": "",
                },
            )
        assert request.url.path == "/api/v1/job"
        return httpx.Response(
            200,
            json={
                "id": 42,
                "state": "PRINTING",
                "file": {"name": "cube.gcode"},
                "progress": 25,
                "time_printing": 120,
                "time_remaining": 360,
            },
        )

    client = PrusaLinkClient(
        "http://prusa.invalid",
        auth_mode="api_key",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    provider = PrusaLinkProvider(client)

    assert await provider.query_status() == {
        "result": {
            "status": {
                "print_stats": {
                    "state": "printing",
                    "filename": "cube.gcode",
                    "message": "",
                    "print_duration": 120,
                },
                "virtual_sdcard": {"progress": 0.25},
                "heater_bed": {"temperature": 59.5, "target": 60},
                "extruder": {"temperature": 214, "target": 215},
                "prusalink": {"job_id": 42, "time_remaining": 360},
            }
        }
    }


@pytest.mark.asyncio
async def test_octoprint_status_contract_uses_canonical_snapshot_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/printer":
            return httpx.Response(
                200,
                json={
                    "state": {"text": "Printing", "flags": {"printing": True}},
                    "temperature": {
                        "bed": {"actual": 59.5, "target": 60},
                        "tool0": {"actual": 214, "target": 215},
                    },
                },
            )
        assert request.url.path == "/api/job"
        return httpx.Response(
            200,
            json={
                "job": {"file": {"name": "cube.gcode"}},
                "progress": {"completion": 25.0, "printTime": 120},
            },
        )

    client = OctoPrintClient(
        "http://octoprint.invalid",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    provider = OctoPrintProvider(client)

    assert await provider.query_status() == {
        "result": {
            "status": {
                "print_stats": {
                    "state": "printing",
                    "filename": "cube.gcode",
                    "message": "Printing",
                    "print_duration": 120,
                },
                "virtual_sdcard": {"progress": 0.25},
                "heater_bed": {"temperature": 59.5, "target": 60},
                "extruder": {"temperature": 214, "target": 215},
            }
        }
    }


@pytest.mark.asyncio
async def test_centauri_status_contract_uses_canonical_snapshot_shape() -> None:
    status = Status.from_payload(
        {
            "TempOfNozzle": 214.5,
            "TempTargetNozzle": 215,
            "TempOfHotbed": 59.5,
            "TempTargetHotbed": 60,
            "TempOfBox": 31,
            "Message": "Printing",
            "PrintInfo": {
                "Status": 13,
                "Filename": "cube.gcode",
                "Progress": 25,
                "CurrentTicks": 120,
            },
        }
    )
    connection = AsyncMock()
    connection.status.return_value = status

    async def connector(enable_control: bool) -> AsyncMock:
        assert enable_control is False
        return connection

    client = ElegooCentauriClient(
        "192.0.2.20",
        model="elegoo_centauri_carbon",
        connector=connector,
    )
    provider = ElegooCentauriProvider(client)

    assert await provider.query_status() == {
        "result": {
            "status": {
                "print_stats": {
                    "state": "printing",
                    "filename": "cube.gcode",
                    "message": "Printing",
                    "print_duration": 120,
                },
                "virtual_sdcard": {"progress": 0.25},
                "heater_bed": {"temperature": 59.5, "target": 60.0},
                "extruder": {"temperature": 214.5, "target": 215.0},
                "temperature_sensor chamber": {"temperature": 31.0},
            }
        }
    }
    connection.close.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("provider", "supported", "support_level", "requires_ready_before_send"),
    [
        (PrinterProvider.MOONRAKER, frozenset(Capability), "stable", False),
        (
            PrinterProvider.BAMBU_LAN,
            frozenset(
                {
                    Capability.START,
                    Capability.PAUSE,
                    Capability.RESUME,
                    Capability.CANCEL,
                    Capability.LIVE_STATUS,
                    Capability.UPLOAD,
                    Capability.MATERIAL_STATE,
                }
            ),
            "beta",
            True,
        ),
        (
            PrinterProvider.PRUSALINK,
            frozenset(
                {
                    Capability.START,
                    Capability.PAUSE,
                    Capability.RESUME,
                    Capability.CANCEL,
                    Capability.LIVE_STATUS,
                    Capability.UPLOAD,
                    Capability.LIST_FILES,
                    Capability.DELETE_FILE,
                    Capability.SERVER_INFO,
                }
            ),
            "beta",
            False,
        ),
        (
            PrinterProvider.OCTOPRINT,
            frozenset(
                {
                    Capability.START,
                    Capability.PAUSE,
                    Capability.RESUME,
                    Capability.CANCEL,
                    Capability.LIVE_STATUS,
                    Capability.UPLOAD,
                    Capability.LIST_FILES,
                    Capability.DELETE_FILE,
                    Capability.SERVER_INFO,
                }
            ),
            "beta",
            False,
        ),
        (
            PrinterProvider.ELEGOO_CENTAURI,
            frozenset(
                {
                    Capability.START,
                    Capability.PAUSE,
                    Capability.RESUME,
                    Capability.CANCEL,
                    Capability.LIVE_STATUS,
                    Capability.SERVER_INFO,
                    Capability.UPLOAD,
                }
            ),
            "beta",
            False,
        ),
    ],
)
def test_provider_capability_contract_is_characterized(
    provider: PrinterProvider,
    supported: frozenset[Capability],
    support_level: str,
    requires_ready_before_send: bool,
) -> None:
    capabilities = capabilities_for_provider(provider)

    assert capabilities.supported == supported
    assert capabilities.support_level == support_level
    assert capabilities.requires_ready_before_send is requires_ready_before_send


def test_artifact_capture_is_an_optional_bambu_only_extension() -> None:
    providers = {
        PrinterProvider.MOONRAKER: MoonrakerProvider("http://printer.invalid"),
        PrinterProvider.BAMBU_LAN: BambuLanProvider(
            "192.0.2.10", "TEST-SERIAL", "test-code"
        ),
        PrinterProvider.PRUSALINK: PrusaLinkProvider(
            PrusaLinkClient(
                "http://prusa.invalid", auth_mode="api_key", api_key="test-key"
            )
        ),
        PrinterProvider.OCTOPRINT: OctoPrintProvider(
            OctoPrintClient("http://octoprint.invalid", api_key="test-key")
        ),
        PrinterProvider.ELEGOO_CENTAURI: ElegooCentauriProvider(
            ElegooCentauriClient("192.0.2.20", model="elegoo_centauri_carbon")
        ),
    }

    assert {
        provider: hasattr(client, "download_artifact")
        for provider, client in providers.items()
    } == {
        PrinterProvider.MOONRAKER: False,
        PrinterProvider.BAMBU_LAN: True,
        PrinterProvider.PRUSALINK: False,
        PrinterProvider.OCTOPRINT: False,
        PrinterProvider.ELEGOO_CENTAURI: False,
    }


def test_bambu_project_request_exposes_current_capture_hint_shape() -> None:
    provider = BambuLanProvider("192.0.2.10", "TEST-SERIAL", "test-code")

    assert provider._normalize_project_request(
        {
            "print": {
                "command": "project_file",
                "url": "ftps://TEST-SERIAL/cache/benchy.3mf",
                "gcode_state": "RUNNING",
                "subtask_name": "Benchy",
                "task_id": "task-42",
            }
        }
    ) == {
        "print_stats": {
            "state": "printing",
            "filename": "Benchy",
            "external_display_name": "Benchy",
            "external_task_id": "task-42",
            "external_artifact_path": "ftps://TEST-SERIAL/cache/benchy.3mf",
        }
    }


@pytest.mark.asyncio
async def test_bambu_download_artifact_extension_delegates_with_byte_limit(
    tmp_path: Path,
) -> None:
    provider = BambuLanProvider("192.0.2.10", "TEST-SERIAL", "test-code")
    destination = tmp_path / "benchy.3mf"

    with patch.object(provider, "_download_via_ftps") as download:
        result = await provider.download_artifact(
            "/cache/benchy.3mf", destination, max_bytes=4096
        )

    assert result is None
    download.assert_called_once_with("/cache/benchy.3mf", destination, max_bytes=4096)
