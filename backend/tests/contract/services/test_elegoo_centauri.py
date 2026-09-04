"""Integration pack for Centauri CC1 SDCP WebSocket and CC2 connection seam."""

from __future__ import annotations

import asyncio

import pycentauri.client as pycentauri_client
import pytest

from app.db.models import PrinterProvider
from app.services.elegoo_centauri import ElegooCentauriClient
from app.services.printer_provider import (
    ElegooCentauriProvider,
    ProviderError,
    build_provider_registry,
    get_provider_client,
)
from tests.factories import printer_config
from tests.fakes.mock_centauri import make_connector, start_cc1_server
from tests.fakes.print_sim import PrintSim

REMOTE = "demo.gcode"
REGISTRY = build_provider_registry()


class TestStart:
    def test_cc1_real_sdcp_websocket_round_trip(self, monkeypatch) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        running = start_cc1_server(sim)
        monkeypatch.setattr(pycentauri_client, "WS_PORT", running.port)
        provider = ElegooCentauriProvider(
            ElegooCentauriClient("127.0.0.1", model="elegoo_centauri_carbon")
        )
        try:

            async def _run() -> None:
                await provider.start(REMOTE)
                await _wait_state(provider, "printing")
                await provider.pause()
                await _wait_state(provider, "paused")
                await provider.resume()
                await _wait_state(provider, "printing")
                await provider.cancel()
                await _wait_state(provider, "cancelled")

            asyncio.run(_run())
        finally:
            running.stop()

    def test_send_print_completes(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=1.0)
        provider, connection = _provider(sim)

        async def _run() -> None:
            await provider.start(REMOTE)
            assert connection.calls[0] == (
                "start_print",
                (
                    REMOTE,
                    {"storage": "local", "auto_leveling": True, "timelapse": False},
                ),
            )
            await _wait_state(provider, "complete")

        asyncio.run(_run())


def _provider(
    sim: PrintSim, *, model: str = "elegoo_centauri_carbon", **connector_kwargs
):
    connector, connection = make_connector(sim, **connector_kwargs)
    client = ElegooCentauriClient(
        "192.0.2.10",
        model=model,
        access_code=connector_kwargs.get("given_access_code"),
        connector=connector,
    )
    return ElegooCentauriProvider(client), connection


async def _wait_state(provider, state: str, *, timeout: float = 10.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await provider.query_status()
        if result["result"]["status"]["print_stats"]["state"] == state:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached state {state!r}")


class TestConnect:
    def test_carbon2_missing_access_code_rejected_at_build(self) -> None:
        # This guard lives in ElegooCentauriProvider.build() (Printer-row level),
        # not the client — a Carbon 2 printer row saved without an access code
        # must never reach the network.
        printer = printer_config(
            "Carbon 2",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon_2",
            elegoo_centauri_host="192.0.2.10",
            elegoo_centauri_access_code=None,
        )
        with pytest.raises(ProviderError) as exc_info:
            get_provider_client(printer, registry=REGISTRY)
        assert exc_info.value.code == "provider_credentials_missing"


class TestRaises:
    def test_carbon2_invalid_access_code_raises_authentication_error(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        provider, _connection = _provider(
            sim,
            model="elegoo_centauri_carbon_2",
            expected_access_code="correct-code",
            given_access_code="wrong-code",
        )

        async def _run() -> None:
            with pytest.raises(ProviderError) as exc_info:
                await provider.query_status()
            assert exc_info.value.code == "provider_authentication_failed"

        asyncio.run(_run())

    def test_network_drop_mid_print_raises_transport_error(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        provider, _connection = _provider(sim, fail_after_connects=1)

        async def _run() -> None:
            await provider.start(REMOTE)
            with pytest.raises(ProviderError) as exc_info:
                await provider.query_status()
            assert exc_info.value.code == "provider_transport_error"

        asyncio.run(_run())


class TestResume:
    def test_pause_then_resume_runs_to_completion(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=1.5)
        provider, connection = _provider(sim)

        async def _run() -> None:
            await provider.start(REMOTE)
            await provider.pause()
            await _wait_state(provider, "paused")
            await provider.resume()
            await _wait_state(provider, "complete")
            assert ("pause", None) in connection.calls
            assert ("resume", None) in connection.calls

        asyncio.run(_run())


class TestCancel:
    def test_cancel_reports_cancelled(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        provider, connection = _provider(sim)

        async def _run() -> None:
            await provider.start(REMOTE)
            await _wait_state(provider, "printing")
            await provider.cancel()
            await _wait_state(provider, "cancelled")
            assert ("stop", None) in connection.calls

        asyncio.run(_run())
