from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pycentauri.client import Printer, PrinterError
from pycentauri.models import Status

from printstash_core.printers.contracts import PrinterClient
from printstash_core.printers.elegoo_centauri import (
    ElegooCentauriClient,
    ElegooCentauriError,
    ElegooCentauriFactory,
)
from printstash_core.printers.models import ElegooCentauriConfig, PrinterSnapshot


def _status(code: int = 13) -> Status:
    return Status.from_payload(
        {
            "TempOfNozzle": 214.5,
            "TempTargetNozzle": 215,
            "TempOfHotbed": 59.5,
            "TempTargetHotbed": 60,
            "TempOfBox": 31,
            "Message": "Printing",
            "PrintInfo": {
                "Status": code,
                "Filename": "cube.gcode",
                "Progress": 25,
                "CurrentTicks": 120,
            },
        }
    )


class FakeConnection:
    def __init__(
        self, status: Status | None = None, *, enable_control: bool = True
    ) -> None:
        self.current_status = status or _status()
        self.enable_control = enable_control
        self.closed = False
        self.calls: list[tuple[str, Any]] = []

    def _require_control(self, action: str) -> None:
        if not self.enable_control:
            raise PrinterError(f"{action} requires enable_control=True")

    async def status(self) -> Status:
        return self.current_status

    async def watch(self):
        yield self.current_status

    async def upload_file(
        self, local_path: str | Path, *, remote_name: str | None = None
    ) -> str:
        self._require_control("upload_file")
        self.calls.append(("upload", (local_path, remote_name)))
        return remote_name or str(local_path)

    async def start_print(self, filename: str) -> dict[str, Any]:
        """Old pycentauri releases accepted no start options."""

        self._require_control("start_print")
        self.calls.append(("start", filename))
        return {}

    async def pause(self) -> dict[str, Any]:
        self._require_control("pause")
        self.calls.append(("pause", None))
        return {}

    async def resume(self) -> dict[str, Any]:
        self._require_control("resume")
        self.calls.append(("resume", None))
        return {}

    async def stop(self) -> dict[str, Any]:
        self._require_control("stop")
        self.calls.append(("stop", None))
        return {}

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_status_and_snapshot_preserve_legacy_shape() -> None:
    connections: list[FakeConnection] = []

    async def connector(enable_control: bool) -> FakeConnection:
        connection = FakeConnection(enable_control=enable_control)
        connections.append(connection)
        return connection

    client = ElegooCentauriClient(
        ElegooCentauriConfig(
            "192.168.1.50", "elegoo_centauri_carbon"
        ),
        connector=connector,
    )
    legacy = await client.query_status()
    snapshot = await client.query_snapshot()

    assert legacy == {
        "result": {
            "status": {
                "print_stats": {
                    "state": "printing",
                    "filename": "cube.gcode",
                    "message": "Printing",
                    "print_duration": 120.0,
                },
                "virtual_sdcard": {"progress": 0.25},
                "heater_bed": {"temperature": 59.5, "target": 60.0},
                "extruder": {"temperature": 214.5, "target": 215.0},
                "temperature_sensor chamber": {"temperature": 31.0},
            }
        }
    }
    assert snapshot == PrinterSnapshot.from_legacy_payload(legacy)
    assert all(connection.closed for connection in connections)


@pytest.mark.asyncio
async def test_controls_upload_and_old_start_signature_remain_compatible(
    tmp_path: Path,
) -> None:
    connections: list[FakeConnection] = []

    async def connector(enable_control: bool) -> FakeConnection:
        connection = FakeConnection(enable_control=enable_control)
        connections.append(connection)
        return connection

    client = ElegooCentauriClient(
        ElegooCentauriConfig(
            "192.168.1.50", "elegoo_centauri_carbon_2", "ABC123"
        ),
        connector=connector,
    )
    source = tmp_path / "cube.gcode"
    source.write_text("G28\n")

    assert await client.upload(source, "cube.gcode") == {"result": "cube.gcode"}
    assert await client.start("cube.gcode") == {"ok": True}
    assert await client.pause() == {"ok": True}
    assert await client.resume() == {"ok": True}
    assert await client.cancel() == {"ok": True}

    assert [connection.calls[0][0] for connection in connections] == [
        "upload",
        "start",
        "pause",
        "resume",
        "stop",
    ]
    assert all(connection.enable_control for connection in connections)
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "standby"),
        (6, "paused"),
        (8, "cancelled"),
        (9, "complete"),
        (14, "error"),
        (27, "paused"),
        (29, "paused"),
        (999, "unknown"),
    ],
)
def test_status_codes_are_version_independent(code: int, expected: str) -> None:
    normalized = ElegooCentauriClient.normalize_status(_status(code))
    assert normalized["print_stats"]["state"] == expected


@pytest.mark.asyncio
async def test_connect_filters_kwargs_missing_from_older_pycentauri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(enable_control=False)

    async def legacy_connect(
        host: str, *, enable_control: bool = False
    ) -> FakeConnection:
        assert host == "192.168.1.50"
        assert enable_control is False
        return connection

    monkeypatch.setattr(Printer, "connect", legacy_connect)
    client = ElegooCentauriClient(
        ElegooCentauriConfig(
            "192.168.1.50",
            "elegoo_centauri_carbon",
            mainboard_id="mainboard-id",
        )
    )

    assert (await client.query_snapshot()).state == "printing"
    assert connection.closed is True


@pytest.mark.asyncio
async def test_subscription_and_errors_keep_public_behavior() -> None:
    connection = FakeConnection(_status(6), enable_control=False)

    async def connector(_enable_control: bool) -> FakeConnection:
        return connection

    client = ElegooCentauriClient(
        ElegooCentauriConfig(
            "192.168.1.50", "elegoo_centauri_carbon"
        ),
        connector=connector,
    )
    stop = asyncio.Event()
    snapshots: list[PrinterSnapshot] = []

    async def receive(snapshot: PrinterSnapshot) -> None:
        snapshots.append(snapshot)
        stop.set()

    await client.subscribe_snapshots(receive, stop_event=stop)
    assert snapshots[0].state == "paused"
    assert connection.closed is True

    with pytest.raises(ElegooCentauriError) as unsupported:
        await client.delete_file("cube.gcode")
    assert unsupported.value.code == "operation_not_supported_for_provider"


def test_factory_builds_runtime_protocol_client() -> None:
    client = ElegooCentauriFactory().build(
        ElegooCentauriConfig(
            "192.168.1.50", "elegoo_centauri_carbon"
        )
    )
    assert isinstance(client, PrinterClient)
