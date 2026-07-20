from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pycentauri.cc2 import CC2Printer
from pycentauri.client import Printer, PrinterError
from pycentauri.models import Status

from app.services.elegoo_centauri import ElegooCentauriClient, ElegooCentauriError


def _status(code: int = 13) -> Status:
    return Status.from_payload(
        {
            "TempOfNozzle": 214.5,
            "TempTargetNozzle": 215,
            "TempOfHotbed": 59.5,
            "TempTargetHotbed": 60,
            "TempOfBox": 31,
            "PrintInfo": {
                "Status": code,
                "Filename": "cube.gcode",
                "Progress": 25,
                "CurrentTicks": 120,
            },
        }
    )


class FakeConnection:
    def __init__(self, status: Status | None = None) -> None:
        self.current_status = status or _status()
        self.closed = False
        self.calls: list[tuple[str, Any]] = []

    async def status(self) -> Status:
        return self.current_status

    async def watch(self):
        yield self.current_status

    async def start_print(self, filename: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("start", (filename, kwargs)))
        return {}

    async def pause(self) -> dict[str, Any]:
        self.calls.append(("pause", None))
        return {}

    async def resume(self) -> dict[str, Any]:
        self.calls.append(("resume", None))
        return {}

    async def stop(self) -> dict[str, Any]:
        self.calls.append(("stop", None))
        return {}

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_centauri_status_is_normalized_and_connection_closed() -> None:
    connection = FakeConnection()

    async def connector(enable_control: bool) -> FakeConnection:
        assert enable_control is False
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50",
        model="elegoo_centauri_carbon",
        connector=connector,
    )
    result = await client.query_status()
    status = result["result"]["status"]
    assert status["print_stats"]["state"] == "printing"
    assert status["print_stats"]["filename"] == "cube.gcode"
    assert status["virtual_sdcard"]["progress"] == 0.25
    assert status["extruder"] == {"temperature": 214.5, "target": 215.0}
    assert connection.closed is True


@pytest.mark.parametrize(
    ("code", "expected"),
    [(0, "standby"), (6, "paused"), (8, "cancelled"), (9, "complete"), (14, "error")],
)
def test_centauri_lifecycle_mapping(code: int, expected: str) -> None:
    assert (
        ElegooCentauriClient.normalize_status(_status(code))["print_stats"]["state"]
        == expected
    )


@pytest.mark.asyncio
async def test_centauri_controls_use_control_enabled_connections() -> None:
    connections: list[FakeConnection] = []

    async def connector(enable_control: bool) -> FakeConnection:
        assert enable_control is True
        connection = FakeConnection()
        connections.append(connection)
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50",
        model="elegoo_centauri_carbon_2",
        access_code="ABC123",
        connector=connector,
    )
    await client.start("cube.gcode")
    await client.pause()
    await client.resume()
    await client.cancel()
    assert [connection.calls[0][0] for connection in connections] == [
        "start",
        "pause",
        "resume",
        "stop",
    ]
    assert all(connection.closed for connection in connections)


@pytest.mark.asyncio
async def test_network_drop_during_action_becomes_provider_error() -> None:
    connection = FakeConnection()

    async def failing_status() -> Status:
        raise OSError("connection reset")

    connection.status = failing_status  # type: ignore[method-assign]

    async def connector(enable_control: bool) -> FakeConnection:
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50",
        model="elegoo_centauri_carbon",
        connector=connector,
    )
    with pytest.raises(ElegooCentauriError):
        await client.query_status()
    assert connection.closed is True


@pytest.mark.asyncio
async def test_close_failure_in_finally_is_swallowed() -> None:
    connection = FakeConnection()

    async def failing_close() -> None:
        raise OSError("already gone")

    connection.close = failing_close  # type: ignore[method-assign]

    async def connector(enable_control: bool) -> FakeConnection:
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50",
        model="elegoo_centauri_carbon",
        connector=connector,
    )
    result = await client.query_status()
    assert result["result"]["status"]["print_stats"]["state"] == "printing"


@pytest.mark.asyncio
async def test_subscription_normalizes_status_and_honors_stop_event() -> None:
    connection = FakeConnection(_status(6))

    async def connector(enable_control: bool) -> FakeConnection:
        assert enable_control is False
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50",
        model="elegoo_centauri_carbon",
        connector=connector,
    )
    stop = __import__("asyncio").Event()
    received: list[dict[str, Any]] = []

    async def on_status(status: dict[str, Any]) -> None:
        received.append(status)
        stop.set()

    await client.subscribe_status(on_status, stop_event=stop)
    assert received[0]["print_stats"]["state"] == "paused"
    assert connection.closed is True


@pytest.mark.asyncio
async def test_cc2_without_access_code_raises_credentials_missing() -> None:
    # Exercises the default (real) _connect path, not a fake connector: the
    # access-code guard fires before any network call is attempted, and the
    # `except ElegooCentauriError: raise` clause re-raises it unchanged.
    client = ElegooCentauriClient("192.168.1.50", model="elegoo_centauri_carbon_2")
    with pytest.raises(ElegooCentauriError) as exc_info:
        await client.query_status()
    assert exc_info.value.code == "provider_credentials_missing"


@pytest.mark.asyncio
async def test_connect_wraps_printer_error_as_authentication_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_connect(*args: Any, **kwargs: Any) -> Any:
        raise PrinterError("access denied by printer")

    monkeypatch.setattr(Printer, "connect", fake_connect)

    client = ElegooCentauriClient("192.168.1.50", model="elegoo_centauri_carbon")
    with pytest.raises(ElegooCentauriError) as exc_info:
        await client.query_status()
    assert exc_info.value.code == "provider_authentication_failed"


@pytest.mark.asyncio
async def test_connect_wraps_printer_error_as_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_connect(*args: Any, **kwargs: Any) -> Any:
        raise PrinterError("socket closed unexpectedly")

    monkeypatch.setattr(CC2Printer, "connect", fake_connect)

    client = ElegooCentauriClient(
        "192.168.1.50", model="elegoo_centauri_carbon_2", access_code="ABC123"
    )
    with pytest.raises(ElegooCentauriError) as exc_info:
        await client.query_status()
    assert exc_info.value.code == "provider_transport_error"


@pytest.mark.asyncio
async def test_connect_wraps_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_connect(*args: Any, **kwargs: Any) -> Any:
        raise OSError("network unreachable")

    monkeypatch.setattr(Printer, "connect", fake_connect)

    client = ElegooCentauriClient("192.168.1.50", model="elegoo_centauri_carbon")
    with pytest.raises(ElegooCentauriError):
        await client.query_status()


@pytest.mark.asyncio
async def test_with_connection_reraises_elegoo_error_unchanged() -> None:
    connection = FakeConnection()

    async def failing_status() -> Status:
        raise ElegooCentauriError("already wrapped", code="provider_credentials_missing")

    connection.status = failing_status  # type: ignore[method-assign]

    async def connector(enable_control: bool) -> FakeConnection:
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50", model="elegoo_centauri_carbon", connector=connector
    )
    with pytest.raises(ElegooCentauriError) as exc_info:
        await client.query_status()
    assert exc_info.value.code == "provider_credentials_missing"
    assert connection.closed is True


@pytest.mark.asyncio
async def test_with_connection_wraps_action_printer_error() -> None:
    connection = FakeConnection()

    async def failing_status() -> Status:
        raise PrinterError("printer went away mid-request")

    connection.status = failing_status  # type: ignore[method-assign]

    async def connector(enable_control: bool) -> FakeConnection:
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50", model="elegoo_centauri_carbon", connector=connector
    )
    with pytest.raises(ElegooCentauriError):
        await client.query_status()
    assert connection.closed is True


@pytest.mark.asyncio
async def test_info_returns_provider_details() -> None:
    client = ElegooCentauriClient(
        "192.168.1.50", model="elegoo_centauri_carbon", access_code="ABC123"
    )
    result = await client.info()
    assert result == {
        "result": {
            "provider": "elegoo_centauri",
            "model": "elegoo_centauri_carbon",
            "host": "192.168.1.50",
        }
    }


@pytest.mark.asyncio
async def test_subscribe_status_wraps_printer_error_from_watch() -> None:
    connection = FakeConnection()

    async def failing_watch():
        raise PrinterError("watch stream dropped")
        yield  # pragma: no cover - makes this an async generator

    connection.watch = failing_watch  # type: ignore[method-assign]

    async def connector(enable_control: bool) -> FakeConnection:
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50", model="elegoo_centauri_carbon", connector=connector
    )
    with pytest.raises(ElegooCentauriError):
        await client.subscribe_status(lambda status: asyncio.sleep(0))
    assert connection.closed is True


@pytest.mark.asyncio
async def test_subscribe_status_wraps_oserror_from_watch() -> None:
    connection = FakeConnection()

    async def failing_watch():
        raise OSError("connection reset")
        yield  # pragma: no cover - makes this an async generator

    connection.watch = failing_watch  # type: ignore[method-assign]

    async def connector(enable_control: bool) -> FakeConnection:
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50", model="elegoo_centauri_carbon", connector=connector
    )
    with pytest.raises(ElegooCentauriError):
        await client.subscribe_status(lambda status: asyncio.sleep(0))
    assert connection.closed is True


@pytest.mark.asyncio
async def test_subscribe_status_close_failure_is_swallowed() -> None:
    connection = FakeConnection()

    async def failing_close() -> None:
        raise OSError("already gone")

    connection.close = failing_close  # type: ignore[method-assign]

    async def connector(enable_control: bool) -> FakeConnection:
        return connection

    client = ElegooCentauriClient(
        "192.168.1.50", model="elegoo_centauri_carbon", connector=connector
    )
    received: list[dict[str, Any]] = []

    async def on_status(status: dict[str, Any]) -> None:
        received.append(status)

    # No stop_event: watch() yields exactly one status then the fake
    # generator is exhausted, so subscribe_status returns normally and hits
    # the finally-block close(), whose failure must not propagate.
    await client.subscribe_status(on_status)
    assert received[0]["print_stats"]["state"] == "printing"
