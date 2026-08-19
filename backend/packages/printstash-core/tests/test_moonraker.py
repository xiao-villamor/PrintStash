from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from printstash_core.printers.contracts import PrinterClient
from printstash_core.printers.models import MoonrakerConfig, PrinterSnapshot
from printstash_core.printers.moonraker import (
    SUBSCRIPTIONS,
    MoonrakerClient,
    MoonrakerError,
    MoonrakerFactory,
)


def _client(handler: Any, *, api_key: str | None = "secret") -> MoonrakerClient:
    transport = httpx.MockTransport(handler)
    return MoonrakerClient(
        MoonrakerConfig("http://printer.local:7125/", api_key),
        http_client_factory=lambda: httpx.AsyncClient(transport=transport),
    )


@pytest.mark.asyncio
async def test_typed_config_preserves_query_wire_shape_and_snapshot() -> None:
    seen: list[httpx.Request] = []
    payload = {
        "result": {
            "status": {
                "print_stats": {"state": "printing", "filename": "cube.gcode"},
                "virtual_sdcard": {"progress": 0.25},
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/server/spoolman/spool_id":
            return httpx.Response(200, json={"result": {"spool_id": 42}})
        return httpx.Response(200, json=payload)

    client = _client(handler)
    try:
        status = await client.query_status()
        snapshot = await client.query_snapshot()
    finally:
        await client.aclose()

    assert (
        status["result"]["status"]["print_stats"]
        == payload["result"]["status"]["print_stats"]
    )
    assert snapshot == PrinterSnapshot.from_legacy_payload(status)
    assert seen[0].headers["X-Api-Key"] == "secret"
    assert seen[0].url.path == "/printer/objects/query"
    assert set(seen[0].url.params) == set(SUBSCRIPTIONS)
    assert snapshot.material_slots[0].external_spool_id == 42
    assert snapshot.material_slots[0].state == "loaded"


@pytest.mark.asyncio
async def test_missing_spoolman_integration_reports_unknown_material_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/server/spoolman/spool_id":
            return httpx.Response(404, json={"error": "not configured"})
        return httpx.Response(
            200,
            json={"result": {"status": {"print_stats": {"state": "standby"}}}},
        )

    client = _client(handler)
    try:
        snapshot = await client.query_snapshot()
    finally:
        await client.aclose()

    assert snapshot.material_slots[0].state == "unknown"
    assert snapshot.material_slots[0].external_spool_id is None


@pytest.mark.asyncio
async def test_legacy_and_neutral_action_names_use_identical_wire_calls(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        encoded_path = request.url.raw_path.split(b"?", 1)[0].decode()
        seen.append((request.method, encoded_path))
        if request.url.path == "/server/files/list":
            return httpx.Response(200, json={"result": [{"path": "cube.gcode"}]})
        return httpx.Response(200, json={"result": "ok"})

    source = tmp_path / "cube.gcode"
    source.write_text("G28\n")
    client = _client(handler)
    try:
        assert await client.list_files() == [{"path": "cube.gcode"}]
        assert await client.upload(source, "cube.gcode") == {"result": "ok"}
        assert await client.start("folder/cube.gcode") == {"result": "ok"}
        await client.pause()
        await client.resume_print()
        await client.cancel()
        await client.delete_file("folder/my part.gcode")
    finally:
        await client.aclose()

    assert ("POST", "/server/files/upload") in seen
    assert ("POST", "/printer/print/start") in seen
    assert ("POST", "/printer/print/pause") in seen
    assert ("POST", "/printer/print/resume") in seen
    assert ("POST", "/printer/print/cancel") in seen
    assert ("DELETE", "/server/files/gcodes/folder/my%20part.gcode") in seen


@pytest.mark.asyncio
async def test_http_failures_keep_legacy_error_surface() -> None:
    client = _client(lambda _request: httpx.Response(503, text="offline"))
    try:
        with pytest.raises(MoonrakerError, match="moonraker 503: offline"):
            await client.info()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_websocket_legacy_and_snapshot_callbacks_share_payload() -> None:
    status = {
        "print_stats": {"state": "paused", "filename": "cube.gcode"},
        "virtual_sdcard": {"progress": 0.5},
    }
    sent: list[dict[str, Any]] = []
    stop = asyncio.Event()

    class WebSocket:
        async def send(self, raw: str) -> None:
            sent.append(json.loads(raw))

        async def recv(self) -> str:
            return json.dumps(
                {"jsonrpc": "2.0", "id": sent[-1]["id"], "result": {"status": status}}
            )

        async def ping(self) -> None:
            return None

    class Context:
        async def __aenter__(self) -> WebSocket:
            return WebSocket()

        async def __aexit__(self, *_args: object) -> None:
            return None

    client = MoonrakerClient(
        MoonrakerConfig("http://printer.local:7125"),
        websocket_connector=lambda *_args, **_kwargs: Context(),
    )
    received: list[PrinterSnapshot] = []

    async def on_snapshot(snapshot: PrinterSnapshot) -> None:
        received.append(snapshot)
        stop.set()

    try:
        await client.subscribe_snapshots(on_snapshot, stop_event=stop)
    finally:
        await client.aclose()

    assert received[0].to_legacy_payload() == status
    assert sent[0]["method"] == "printer.objects.subscribe"


def test_factory_builds_runtime_protocol_client() -> None:
    client = MoonrakerFactory().build(MoonrakerConfig("http://printer.local"))
    assert isinstance(client, PrinterClient)
