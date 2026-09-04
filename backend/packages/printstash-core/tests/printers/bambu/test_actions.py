"""What a caller can ask a Bambu printer to do, and what it must refuse.

Bambu LAN mode is a *beta* provider with a deliberately narrow surface: it can
start, pause, resume, cancel, upload, and report status, and it cannot list or
delete printer files, run raw G-code, or stop in an emergency. That narrowness is
a published contract (`docs/provider-support.md`), and it has to be enforced
before any transport is touched — a "not supported" that only surfaces as a
timeout or a `NotImplementedError` from a worker thread reads as a broken printer
rather than an unsupported feature.

The commands that *are* supported carry wire payloads Bambu accepts literally:
the start command names an absolute `/cache/` path, and pause/resume/cancel use
sequence id `0`, which is what the printer's own app sends. These are pinned
byte-for-byte because a plausible-looking variant is accepted by the MQTT broker
and then silently ignored by the printer.

Uploads and downloads are the byte paths, asserted here at the public seam: the
guarantee is that a failure never leaves a half-written artifact behind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from printstash_core.printers.contracts import ArtifactCaptureClient, PrinterClient
from printstash_core.printers.models import ProviderError

from .conftest import HOST, SERIAL, FakeFtpsClient, ScriptedMqttClient, make_client

# The report shape `query_status` accepts as an answer to its `pushall`.
RUNNING_REPORT = {
    "print": {
        "gcode_state": "RUNNING",
        "mc_percent": 25,
        "gcode_file": "/cache/cube.gcode",
        "task_id": "task-42",
    }
}
RUNNING_ENVELOPE = {
    "result": {
        "status": {
            "print_stats": {
                "state": "printing",
                "filename": "cube.gcode",
                "external_task_id": "task-42",
                "external_gcode_file": "/cache/cube.gcode",
            },
            "virtual_sdcard": {"progress": 0.25},
        }
    }
}


class TestInfo:
    @pytest.mark.asyncio
    async def test_identifies_the_printer_without_touching_the_network(self) -> None:
        assert await make_client().info() == {
            "result": {"provider": "bambu_lan", "host": HOST, "serial": SERIAL}
        }


class TestQueryStatus:
    @pytest.mark.asyncio
    async def test_returns_the_normalized_envelope_after_a_full_push(
        self,
    ) -> None:
        wire = ScriptedMqttClient(messages=[RUNNING_REPORT])
        client = make_client(
            mqtt_client_factory=lambda: wire, sequence_id_factory=lambda: "query-id"
        )

        assert await client.query_status() == RUNNING_ENVELOPE
        assert (
            "publish",
            f"device/{SERIAL}/request",
            {
                "pushing": {
                    "sequence_id": "query-id",
                    "command": "pushall",
                    "version": 1,
                    "push_target": 1,
                }
            },
            1,
            False,
        ) in wire.calls

    @pytest.mark.asyncio
    async def test_ignores_a_report_carrying_no_status_fields(self) -> None:
        wire = ScriptedMqttClient(
            messages=[{"print": {"command": "ack"}}, RUNNING_REPORT]
        )
        client = make_client(mqtt_client_factory=lambda: wire)

        # Bambu chatters on the report topic — command acknowledgements arrive
        # there too. Only a report that actually carries status answers a
        # `pushall`, so the acknowledgement is passed over and the next one
        # counts. (Asserted through the value returned rather than a timeout:
        # `query_status` fixes its own 10s wait, and a suite should not spend it.)
        assert await client.query_status() == RUNNING_ENVELOPE

    @pytest.mark.asyncio
    async def test_wraps_an_unexpected_transport_failure(self) -> None:
        client = make_client()

        def explode(*_a: Any, **_k: Any) -> None:
            raise OSError("host unreachable")

        client._mqtt_request = explode  # type: ignore[method-assign]

        with pytest.raises(ProviderError) as error:
            await client.query_status()

        assert error.value.code == "provider_transport_error"


class TestQuerySnapshot:
    @pytest.mark.asyncio
    async def test_returns_the_same_reading_as_the_legacy_envelope(self) -> None:
        wire = ScriptedMqttClient(messages=[RUNNING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)

        snapshot = await client.query_snapshot()

        assert snapshot.state == "printing"
        assert snapshot.filename == "cube.gcode"
        assert snapshot.progress == 0.25
        assert snapshot.print.external_task_id == "task-42"

    @pytest.mark.asyncio
    async def test_round_trips_back_to_the_legacy_envelope_losslessly(self) -> None:
        wire = ScriptedMqttClient(messages=[RUNNING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)

        snapshot = await client.query_snapshot()

        # Both shapes are live: the typed snapshot for new code, the envelope
        # for the existing API surface. A lossy conversion would show one
        # reading in the UI and another in the job record.
        assert snapshot.to_legacy_payload() == RUNNING_ENVELOPE


class TestUpload:
    @pytest.mark.asyncio
    async def test_returns_the_remote_name_it_stored(self, source_file: Path) -> None:
        client = make_client(ftps_client_factory=lambda: FakeFtpsClient())

        assert await client.upload(source_file, "cube.gcode") == {
            "ok": True,
            "remote_filename": "cube.gcode",
        }

    @pytest.mark.asyncio
    async def test_refuses_a_remote_name_containing_a_directory(
        self, source_file: Path
    ) -> None:
        client = make_client(ftps_client_factory=lambda: FakeFtpsClient())

        with pytest.raises(ProviderError) as error:
            await client.upload(source_file, "folder/cube.gcode")

        assert error.value.detail == "invalid_bambu_remote_filename"

    @pytest.mark.asyncio
    async def test_reports_a_size_mismatch_rather_than_claiming_success(
        self, source_file: Path
    ) -> None:
        client = make_client(ftps_client_factory=lambda: FakeFtpsClient(remote_size=99))

        with pytest.raises(ProviderError) as error:
            await client.upload(source_file, "cube.gcode")

        assert error.value.detail == "bambu_upload_size_mismatch"

    @pytest.mark.asyncio
    async def test_wraps_an_unexpected_failure_from_the_transfer(
        self, source_file: Path
    ) -> None:
        def explode() -> Any:
            raise RuntimeError("ftplib blew up in a new way")

        client = make_client(ftps_client_factory=explode)

        with pytest.raises(ProviderError) as error:
            await client.upload(source_file, "cube.gcode")

        assert error.value.code == "provider_error"


class TestDownloadArtifact:
    @pytest.mark.asyncio
    async def test_writes_the_artifact_to_the_destination(self, tmp_path: Path) -> None:
        ftp = FakeFtpsClient(download=b"1234")
        client = make_client(ftps_client_factory=lambda: ftp)
        destination = tmp_path / "benchy.3mf"

        await client.download_artifact(
            f"ftps://{HOST}/cache/benchy.3mf", destination, max_bytes=4
        )

        assert destination.read_bytes() == b"1234"

    @pytest.mark.asyncio
    async def test_leaves_no_file_behind_when_the_path_is_refused(
        self, tmp_path: Path
    ) -> None:
        client = make_client(ftps_client_factory=lambda: FakeFtpsClient())
        destination = tmp_path / "benchy.3mf"

        with pytest.raises(ProviderError):
            await client.download_artifact(
                "ftps://other.invalid/cache/benchy.3mf", destination, max_bytes=4
            )

        # A partial or absent artifact must not be mistaken for the print's
        # captured bytes; the job records a capture failure instead.
        assert not destination.exists()

    @pytest.mark.asyncio
    async def test_leaves_no_file_behind_when_the_artifact_is_too_large(
        self, tmp_path: Path
    ) -> None:
        client = make_client(
            ftps_client_factory=lambda: FakeFtpsClient(download=b"12345")
        )
        destination = tmp_path / "benchy.3mf"

        with pytest.raises(ProviderError) as error:
            await client.download_artifact("benchy.3mf", destination, max_bytes=4)

        assert error.value.detail == "bambu_artifact_too_large"
        assert not destination.exists()

    @pytest.mark.asyncio
    async def test_leaves_no_file_behind_when_the_transfer_fails_unexpectedly(
        self, tmp_path: Path
    ) -> None:
        def explode() -> Any:
            raise RuntimeError("ftplib blew up in a new way")

        client = make_client(ftps_client_factory=explode)
        destination = tmp_path / "benchy.3mf"

        with pytest.raises(ProviderError):
            await client.download_artifact("benchy.3mf", destination, max_bytes=4)

        assert not destination.exists()


class TestStart:
    @pytest.mark.asyncio
    async def test_sends_the_absolute_cache_path_bambu_expects(self) -> None:
        payloads: list[dict[str, Any]] = []
        client = make_client(sequence_id_factory=lambda: "sequence-42")
        client._send_command = _recorder(payloads)  # type: ignore[method-assign]

        assert await client.start("cube.gcode") == {"ok": True}
        assert payloads == [
            {
                "print": {
                    "sequence_id": "sequence-42",
                    "command": "gcode_file",
                    "param": "/cache/cube.gcode",
                }
            }
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("remote_filename", ["folder/cube.gcode", "/cube.gcode"])
    async def test_refuses_a_name_that_is_not_a_bare_filename(
        self, remote_filename: str
    ) -> None:
        client = make_client()

        with pytest.raises(ProviderError) as error:
            await client.start(remote_filename)

        assert error.value.detail == "invalid_bambu_remote_filename"


class TestPause:
    @pytest.mark.asyncio
    async def test_sends_the_pause_command_bambus_own_app_sends(self) -> None:
        payloads: list[dict[str, Any]] = []
        client = make_client()
        client._send_command = _recorder(payloads)  # type: ignore[method-assign]

        assert await client.pause() == {"ok": True}
        assert payloads == [{"print": {"sequence_id": "0", "command": "pause"}}]


class TestResume:
    @pytest.mark.asyncio
    async def test_sends_the_resume_command(self) -> None:
        payloads: list[dict[str, Any]] = []
        client = make_client()
        client._send_command = _recorder(payloads)  # type: ignore[method-assign]

        assert await client.resume() == {"ok": True}
        assert payloads == [{"print": {"sequence_id": "0", "command": "resume"}}]


class TestCancel:
    @pytest.mark.asyncio
    async def test_sends_stop_rather_than_cancel(self) -> None:
        payloads: list[dict[str, Any]] = []
        client = make_client()
        client._send_command = _recorder(payloads)  # type: ignore[method-assign]

        assert await client.cancel() == {"ok": True}
        # Bambu's vocabulary: there is no `cancel`, and a command it does not
        # recognize is accepted by the broker and then ignored.
        assert payloads == [{"print": {"sequence_id": "0", "command": "stop"}}]


class TestUnsupportedOperations:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "arguments"),
        [
            ("list_files", ()),
            ("delete_file", ("cube.gcode",)),
            ("run_gcode", ("G28",)),
            ("emergency_stop", ()),
            ("server_info", ()),
            ("server_config", ()),
            ("printer_config", ()),
        ],
    )
    async def test_refuses_before_opening_a_connection(
        self, method: str, arguments: tuple[Any, ...]
    ) -> None:
        # Bambu LAN mode genuinely cannot do these. Refusing at the seam keeps
        # the message "unsupported" rather than a timeout that reads as a
        # broken printer — and keeps `NotImplementedError` unreachable.
        client = make_client()

        with pytest.raises(ProviderError) as error:
            await getattr(client, method)(*arguments)

        assert error.value.code == "operation_not_supported_for_provider"


class TestCapabilities:
    def test_declares_itself_beta(self) -> None:
        # `docs/provider-support.md` publishes this level; the UI shows it.
        assert make_client().capabilities.support_level == "beta"

    def test_requires_the_printer_to_be_ready_before_sending(self) -> None:
        # Bambu accepts an upload while printing and then loses it.
        assert make_client().capabilities.requires_ready_before_send is True

    def test_satisfies_the_neutral_printer_protocol(self) -> None:
        assert isinstance(make_client(), PrinterClient)

    def test_satisfies_the_artifact_capture_protocol(self) -> None:
        # Capture is what makes an externally-started Bambu print reproducible.
        assert isinstance(make_client(), ArtifactCaptureClient)


def _recorder(payloads: list[dict[str, Any]]) -> Any:
    async def send(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        return {"ok": True}

    return send
