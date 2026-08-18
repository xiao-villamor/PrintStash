from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from printstash_core.printers.contracts import PrinterClient
from printstash_core.printers.models import (
    OctoPrintConfig,
    PrinterSnapshot,
    ProviderError,
    PrusaLinkConfig,
)
from printstash_core.printers.prusalink import (
    PrusaLinkClient,
    PrusaLinkError,
    PrusaLinkFactory,
)


def _client(handler: Any, *, auth_mode: str = "api_key") -> PrusaLinkClient:
    return PrusaLinkClient(
        PrusaLinkConfig(
            "http://prusa.local/",
            auth_mode,
            username="maker",
            password="secret",
            api_key="key-123",
        ),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_status_wire_shape_and_neutral_snapshot_are_exact() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "key-123"
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
                    }
                },
            )
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

    client = _client(handler)
    legacy = await client.query_status()
    snapshot = await client.query_snapshot()

    assert legacy == {
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
    assert snapshot == PrinterSnapshot.from_legacy_payload(legacy)


@pytest.mark.asyncio
async def test_file_operations_preserve_paths_and_control_calls(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/v1/files/local/":
            return httpx.Response(
                200,
                json={
                    "children": [
                        {
                            "name": "sub",
                            "type": "FOLDER",
                            "children": [
                                {"name": "cube.gcode", "type": "PRINT_FILE"}
                            ],
                        }
                    ]
                },
            )
        if request.url.path == "/api/v1/job" and request.method == "GET":
            return httpx.Response(200, json={"id": 7})
        return httpx.Response(204)

    source = tmp_path / "cube.gcode"
    source.write_text("G28\n")
    client = _client(handler)

    assert [item["path"] for item in await client.list_files()] == [
        "sub/cube.gcode"
    ]
    await client.upload(source, "folder/cube.gcode")
    await client.start("folder/cube.gcode")
    await client.delete_file("folder/cube.gcode")
    await client.pause()
    await client.resume()
    await client.cancel()

    assert ("PUT", "/api/v1/files/local/folder/cube.gcode") in seen
    assert ("POST", "/api/v1/files/local/folder/cube.gcode") in seen
    assert ("DELETE", "/api/v1/files/local/folder/cube.gcode") in seen
    assert ("PUT", "/api/v1/job/7/pause") in seen
    assert ("PUT", "/api/v1/job/7/resume") in seen
    assert ("DELETE", "/api/v1/job/7") in seen


@pytest.mark.asyncio
async def test_errors_and_path_validation_keep_stable_codes(tmp_path: Path) -> None:
    source = tmp_path / "cube.gcode"
    source.write_text("G28\n")
    forbidden = _client(lambda _request: httpx.Response(403))
    with pytest.raises(PrusaLinkError) as auth_error:
        await forbidden.info()
    assert auth_error.value.code == "provider_authentication_failed"

    client = _client(lambda _request: httpx.Response(204))
    with pytest.raises(PrusaLinkError) as path_error:
        await client.upload(source, "../cube.gcode")
    assert path_error.value.code == "provider_error"

    with pytest.raises(PrusaLinkError) as unsupported:
        await client.run_gcode("G28")
    assert unsupported.value.code == "operation_not_supported_for_provider"


@pytest.mark.asyncio
async def test_legacy_subscription_adapts_to_snapshot_callback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/status":
            return httpx.Response(200, json={"printer": {"state": "IDLE"}})
        return httpx.Response(404)

    stop = asyncio.Event()
    stop.set()
    snapshots: list[PrinterSnapshot] = []

    async def receive(snapshot: PrinterSnapshot) -> None:
        snapshots.append(snapshot)

    await _client(handler).subscribe_snapshots(receive, stop_event=stop)

    assert snapshots[0].state == "standby"


def test_factory_builds_protocol_and_rejects_wrong_config() -> None:
    factory = PrusaLinkFactory()
    client = factory.build(
        PrusaLinkConfig("http://prusa.local", "api_key", api_key="key")
    )
    assert isinstance(client, PrinterClient)

    with pytest.raises(ProviderError, match="provider_config_mismatch"):
        factory.build(OctoPrintConfig("http://octoprint.local", "key"))
