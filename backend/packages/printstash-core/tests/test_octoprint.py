from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from printstash_core.printers.contracts import PrinterClient
from printstash_core.printers.models import OctoPrintConfig, PrinterSnapshot
from printstash_core.printers.octoprint import (
    OctoPrintClient,
    OctoPrintError,
    OctoPrintFactory,
)


def _client(handler: Any) -> OctoPrintClient:
    return OctoPrintClient(
        OctoPrintConfig("http://octopi.local/", "key-123"),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_status_wire_shape_and_neutral_snapshot_are_exact() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "key-123"
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
        return httpx.Response(
            200,
            json={
                "job": {"file": {"name": "cube.gcode"}},
                "progress": {"completion": 25.0, "printTime": 120},
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
                    "message": "Printing",
                    "print_duration": 120,
                },
                "virtual_sdcard": {"progress": 0.25},
                "heater_bed": {"temperature": 59.5, "target": 60},
                "extruder": {"temperature": 214, "target": 215},
            }
        }
    }
    assert snapshot == PrinterSnapshot.from_legacy_payload(legacy)


@pytest.mark.asyncio
async def test_file_operations_preserve_streaming_and_nested_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/files":
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "name": "folder",
                            "type": "folder",
                            "children": [
                                {
                                    "name": "cube.gcode",
                                    "path": "folder/cube.gcode",
                                    "type": "machinecode",
                                }
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(200, json={})

    source = tmp_path / "cube.gcode"
    source.write_bytes(b"G28\n" * 100)

    def forbid_read_bytes(_path: Path) -> bytes:
        raise AssertionError("upload must stream from an open file")

    monkeypatch.setattr(Path, "read_bytes", forbid_read_bytes)
    client = _client(handler)
    assert [item["path"] for item in await client.list_files()] == [
        "folder/cube.gcode"
    ]
    await client.upload(source, "sub/dir/cube.gcode")
    await client.start("sub/dir/cube.gcode")
    await client.delete_file("sub/dir/cube.gcode")
    await client.pause()
    await client.resume()
    await client.cancel()

    upload = next(item for item in seen if item[1] == "/api/files/local")
    assert b'name="path"' in upload[2]
    assert b"sub/dir" in upload[2]
    assert ("POST", "/api/files/local/sub/dir/cube.gcode") in {
        (method, path) for method, path, _body in seen
    }


@pytest.mark.asyncio
async def test_error_codes_and_legacy_payloads_remain_stable() -> None:
    forbidden = _client(lambda _request: httpx.Response(403))
    with pytest.raises(OctoPrintError) as auth_error:
        await forbidden.info()
    assert auth_error.value.code == "provider_authentication_failed"

    conflict = _client(lambda _request: httpx.Response(409))
    with pytest.raises(OctoPrintError) as job_error:
        await conflict.pause()
    assert job_error.value.code == "provider_no_active_job"

    no_content = _client(lambda _request: httpx.Response(204))
    assert await no_content.cancel() == {"ok": True}
    with pytest.raises(OctoPrintError) as unsupported:
        await no_content.run_gcode("G28")
    assert unsupported.value.code == "operation_not_supported_for_provider"


@pytest.mark.asyncio
async def test_legacy_subscription_adapts_to_snapshot_callback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/printer":
            return httpx.Response(200, json={"state": {"flags": {}}})
        return httpx.Response(200, json={"job": {}, "progress": {}})

    stop = asyncio.Event()
    stop.set()
    snapshots: list[PrinterSnapshot] = []

    async def receive(snapshot: PrinterSnapshot) -> None:
        snapshots.append(snapshot)

    await _client(handler).subscribe_snapshots(receive, stop_event=stop)
    assert snapshots[0].state == "standby"


def test_factory_builds_runtime_protocol_client() -> None:
    client = OctoPrintFactory().build(
        OctoPrintConfig("http://octoprint.local", "key")
    )
    assert isinstance(client, PrinterClient)
