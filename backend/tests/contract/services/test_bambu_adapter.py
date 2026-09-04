"""Integration pack for the Bambu LAN provider against its three fakes.

``BambuLanProvider`` is not one transport: status polling is real HTTPS
(exercised end to end here against a self-signed loopback server), commands
go over MQTT and uploads over implicit FTPS — neither has a constructor
seam, so those two are patched at the instance level (see
``mock_bambu.py`` docstring).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.printer_provider import BambuLanProvider, ProviderError
from tests.fakes.mock_bambu import (
    FakeFtpTls,
    make_mqtt_factory,
)
from tests.fakes.print_sim import PrintSim

REMOTE = "demo.gcode"
ACCESS_CODE = "12345678"


def _provider(
    sim: PrintSim,
    *,
    printer_access_code: str = ACCESS_CODE,
    expected_access_code: str | None = ACCESS_CODE,
    reject_commands: bool = False,
    pushall_report: dict[str, Any] | None = None,
) -> tuple[BambuLanProvider, list]:
    factory, built = make_mqtt_factory(
        sim,
        expected_access_code=expected_access_code,
        reject_commands=reject_commands,
        pushall_report=pushall_report,
    )
    provider = BambuLanProvider(
        host="127.0.0.1",
        serial="01S00A000000000",
        access_code=printer_access_code,
        mqtt_client_factory=factory,
    )
    return provider, built


async def _wait_state(
    provider: BambuLanProvider, state: str, *, timeout: float = 10.0
) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await provider.query_status()
        if result["result"]["status"]["print_stats"]["state"] == state:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached state {state!r}")


class TestStart:
    def test_send_print_completes(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=1.0)
        provider, built = _provider(sim)

        async def _run() -> None:
            await provider.start(REMOTE)
            assert built[0].published[0]["print"]["command"] == "gcode_file"
            assert built[0].subscriptions == [("device/01S00A000000000/report", 1)]
            assert built[0].published_topics == ["device/01S00A000000000/request"]
            await _wait_state(provider, "complete")

        asyncio.run(_run())
        assert built[0].username == "bblp"
        assert built[0].tls_configured is True


class TestSubscribeStatus:
    def test_status_subscription_bootstraps_once_per_mqtt_session(
        self,
    ) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        provider, built = _provider(sim)

        async def _run() -> None:
            stop = asyncio.Event()
            received: list[dict] = []

            async def on_status(status: dict) -> None:
                received.append(status)
                stop.set()

            await provider.subscribe_status(on_status, stop_event=stop)
            assert received[0]["print_stats"]["state"] == "standby"

        asyncio.run(_run())
        assert len(built) == 1
        assert built[0].subscriptions == [("device/01S00A000000000/report", 1)]
        assert built[0].published_topics == ["device/01S00A000000000/request"]
        assert built[0].published == [
            {
                "pushing": {
                    "command": "pushall",
                    "push_target": 1,
                    "version": 1,
                    "sequence_id": built[0].published[0]["pushing"]["sequence_id"],
                }
            }
        ]

    def test_project_file_report_preserves_external_capture_hint(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        provider, built = _provider(
            sim,
            pushall_report={
                "print": {
                    "command": "project_file",
                    "gcode_state": "RUNNING",
                    "subtask_name": "Benchy",
                    "task_id": "task-42",
                    "url": "ftps://01S00A000000000/cache/benchy.3mf",
                }
            },
        )

        async def _run() -> None:
            stop = asyncio.Event()
            received: list[dict[str, Any]] = []

            async def on_status(status: dict[str, Any]) -> None:
                received.append(status)
                stop.set()

            await provider.subscribe_status(on_status, stop_event=stop)
            assert received == [
                {
                    "print_stats": {
                        "state": "printing",
                        "filename": "Benchy",
                        "external_display_name": "Benchy",
                        "external_task_id": "task-42",
                        "external_artifact_path": "ftps://01S00A000000000/cache/benchy.3mf",
                    }
                }
            ]

        asyncio.run(_run())
        assert built[0].subscriptions == [("device/01S00A000000000/report", 1)]
        assert built[0].published_topics == ["device/01S00A000000000/request"]


class TestResume:
    def test_pause_then_resume_runs_to_completion(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=1.5)
        provider, _built = _provider(sim)

        async def _run() -> None:
            await provider.start(REMOTE)
            await provider.pause()
            await _wait_state(provider, "paused")
            await provider.resume()
            await _wait_state(provider, "complete")

        asyncio.run(_run())


class TestCancel:
    def test_cancel_reports_finish(self) -> None:
        # Bambu's gcode_state has no distinct "cancelled" value — a stopped print
        # reports FINISH, same as a completed one (see _SIM_TO_BAMBU_STATE).
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        provider, built = _provider(sim)

        async def _run() -> None:
            await provider.start(REMOTE)
            await _wait_state(provider, "printing")
            await provider.cancel()
            await _wait_state(provider, "complete")
            assert built[-2].published[-1]["print"]["command"] == "stop"

        asyncio.run(_run())


class TestRaises:
    def test_rejected_command_raises_provider_error(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        provider, _built = _provider(sim, reject_commands=True)

        async def _run() -> None:
            with pytest.raises(ProviderError, match="rejected by printer") as exc_info:
                await provider.start(REMOTE)
            assert exc_info.value.code == "provider_command_rejected"

        asyncio.run(_run())

    def test_invalid_access_code_raises_authentication_error(self) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        provider, _built = _provider(
            sim, printer_access_code="wrong-code", expected_access_code=ACCESS_CODE
        )

        async def _run() -> None:
            with pytest.raises(ProviderError) as exc_info:
                await provider.start(REMOTE)
            assert exc_info.value.code == "provider_authentication_failed"

        asyncio.run(_run())

    def test_upload_with_wrong_access_code_raises_transport_error(
        self, tmp_path
    ) -> None:
        provider = BambuLanProvider(
            host="127.0.0.1", serial="01S00A000000000", access_code="wrong"
        )
        fake_ftp = FakeFtpTls(expected_access_code=ACCESS_CODE)
        provider._ftps_client = lambda: fake_ftp

        local = tmp_path / "demo.gcode"
        local.write_bytes(b"; mock gcode payload\n")

        async def _run() -> None:
            with pytest.raises(ProviderError) as exc_info:
                await provider.upload(local, "demo.gcode")
            assert exc_info.value.code == "provider_transport_error"
            assert exc_info.value.action_code == "bambu_ftps_authentication_failed"

        asyncio.run(_run())


class TestUpload:
    def test_upload_via_ftps_then_start_idle_only(self, tmp_path) -> None:
        sim = PrintSim(total_mm=1000.0, total_seconds=10.0, print_seconds=5.0)
        provider = BambuLanProvider(
            host="127.0.0.1", serial="01S00A000000000", access_code=ACCESS_CODE
        )
        fake_ftp = FakeFtpTls(expected_access_code=ACCESS_CODE)
        provider._ftps_client = lambda: (
            fake_ftp
        )  # instance override; see mock_bambu.py docstring

        local = tmp_path / "demo.gcode"
        local.write_bytes(b"; mock gcode payload\n")

        async def _run() -> None:
            result = await provider.upload(local, "demo.gcode")
            assert result["ok"] is True

        asyncio.run(_run())

        assert fake_ftp.files["cache/demo.gcode"] == b"; mock gcode payload\n"
        assert fake_ftp.username == "bblp"
        assert fake_ftp.private_data is True
        # Upload never starts a print by itself — matches the OctoPrint/PrusaLink
        # security contract of "upload != print".
        assert sim.state == "standby"
