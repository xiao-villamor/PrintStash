"""The long-lived MQTT session that feeds live status.

This is the code path a Bambu printer spends its whole life in, and until now the
only one in the client with no test at all. It is also the hardest: paho runs its
network loop on a second thread, so every status report crosses a thread boundary
via `loop.call_soon_threadsafe` / `run_coroutine_threadsafe` before it reaches an
async callback. Three things have to hold or the printer goes dark.

**It must subscribe and ask for a full push.** Bambu sends nothing unprompted to
a fresh subscriber, so a session that connects without publishing `pushall`
shows a printer that is online and permanently blank.

**A failing callback must not kill the session.** The consumer above is the
printer hub, which writes to the database; one bad report there would otherwise
tear down the subscription and leave the printer stuck as unreachable until a
restart.

**The socket must be released on every exit** — normal stop, connection refused,
or an exception on the way out. A leaked paho loop thread survives the printer
being deleted.

`project_file` traffic is preferred over ordinary status on this topic, because
it is the only frame that carries the artifact path; that preference is what
makes capture of an externally-started print possible at all.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from printstash_core.printers.models import ProviderError

from .conftest import (
    HOST,
    REPORT_TOPIC,
    REQUEST_TOPIC,
    SERIAL,
    ScriptedMqttClient,
    make_client,
)

PRINTING_REPORT = {"print": {"gcode_state": "RUNNING", "mc_percent": 50}}
PROJECT_REPORT = {
    "print": {
        "command": "project_file",
        "url": f"ftps://{SERIAL}/cache/benchy.3mf",
        "gcode_state": "RUNNING",
        "subtask_name": "Benchy",
    }
}


def collector(into: list[dict[str, Any]]) -> Any:
    async def on_status(status: dict[str, Any]) -> None:
        into.append(status)

    return on_status


class TestSubscribeStatus:
    @pytest.mark.asyncio
    async def test_delivers_the_first_status_report(self) -> None:
        wire = ScriptedMqttClient(messages=[PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)
        received: list[dict[str, Any]] = []

        await client.subscribe_status(collector(received))

        assert received == [
            {"print_stats": {"state": "printing"}, "virtual_sdcard": {"progress": 0.5}}
        ]

    @pytest.mark.asyncio
    async def test_asks_for_a_full_push_before_waiting_for_messages(self) -> None:
        wire = ScriptedMqttClient(messages=[PRINTING_REPORT])
        client = make_client(
            mqtt_client_factory=lambda: wire, sequence_id_factory=lambda: "push-id"
        )

        await client.subscribe_status(collector([]))

        # Bambu sends nothing to a fresh subscriber until asked. Without the
        # `pushall`, the printer reads as online and permanently blank.
        assert wire.calls[:4] == [
            ("connect", HOST, 8883, 30),
            ("subscribe", REPORT_TOPIC, 1),
            (
                "publish",
                REQUEST_TOPIC,
                {
                    "pushing": {
                        "sequence_id": "push-id",
                        "command": "pushall",
                        "version": 1,
                        "push_target": 1,
                    }
                },
                1,
                False,
            ),
            ("loop_start",),
        ]

    @pytest.mark.asyncio
    async def test_closes_the_session_on_the_way_out(self) -> None:
        wire = ScriptedMqttClient(messages=[PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)

        await client.subscribe_status(collector([]))

        # A leaked paho loop thread outlives the printer being deleted.
        assert wire.calls[-2:] == [("disconnect",), ("loop_stop",)]

    @pytest.mark.asyncio
    async def test_prefers_the_project_file_frame_that_carries_the_artifact_path(
        self,
    ) -> None:
        wire = ScriptedMqttClient(messages=[PROJECT_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)
        received: list[dict[str, Any]] = []

        await client.subscribe_status(collector(received))

        # This frame is the only one carrying the artifact URL, and it arrives
        # on the same topic as ordinary status. Normalizing it as plain status
        # would drop the path and make capture impossible.
        assert received[0]["print_stats"]["external_artifact_path"] == (
            f"ftps://{SERIAL}/cache/benchy.3mf"
        )

    @pytest.mark.asyncio
    async def test_ignores_a_frame_that_is_not_valid_json(self) -> None:
        wire = ScriptedMqttClient(messages=[b"\xff not json", PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)
        received: list[dict[str, Any]] = []

        await client.subscribe_status(collector(received))

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_ignores_a_frame_with_no_print_section(self) -> None:
        wire = ScriptedMqttClient(messages=[{"info": {}}, PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)
        received: list[dict[str, Any]] = []

        await client.subscribe_status(collector(received))

        # Bambu publishes firmware and HMS frames on the report topic too.
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_ignores_a_frame_that_normalizes_to_nothing(self) -> None:
        wire = ScriptedMqttClient(
            messages=[{"print": {"command": "ack"}}, PRINTING_REPORT]
        )
        client = make_client(mqtt_client_factory=lambda: wire)
        received: list[dict[str, Any]] = []

        await client.subscribe_status(collector(received))

        # A command acknowledgement carries no status; delivering `{}` upward
        # would look like a printer that had gone blank.
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_keeps_the_session_alive_when_the_callback_raises(self) -> None:
        stop = asyncio.Event()
        wire = ScriptedMqttClient(messages=[PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)
        attempts = 0

        async def failing(_status: dict[str, Any]) -> None:
            nonlocal attempts
            attempts += 1
            stop.set()
            raise RuntimeError("the hub could not write that row")

        await client.subscribe_status(failing, stop_event=stop)

        # The consumer is the printer hub writing to the database. One bad row
        # must not tear the subscription down and strand the printer offline.
        assert attempts == 1
        assert wire.calls[-2:] == [("disconnect",), ("loop_stop",)]

    @pytest.mark.asyncio
    async def test_runs_until_the_stop_event_is_set(self) -> None:
        stop = asyncio.Event()
        wire = ScriptedMqttClient(messages=[PRINTING_REPORT, PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)
        received: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            received.append(status)
            if len(received) == 2:
                stop.set()

        await client.subscribe_status(on_status, stop_event=stop)

        # With a stop event the session is long-lived: it keeps delivering
        # rather than returning after the first report.
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_does_not_connect_when_the_stop_event_is_already_set(self) -> None:
        stop = asyncio.Event()
        stop.set()
        wire = ScriptedMqttClient(messages=[PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)

        await client.subscribe_status(collector([]), stop_event=stop)

        # Shutdown races the supervisor starting a subscription; opening a
        # socket that is about to be abandoned leaks a loop thread.
        assert wire.calls == []

    @pytest.mark.asyncio
    async def test_surfaces_a_refused_connection_as_an_authentication_failure(
        self,
    ) -> None:
        wire = ScriptedMqttClient(reason_code=5)
        client = make_client(mqtt_client_factory=lambda: wire)

        with pytest.raises(ProviderError) as error:
            await client.subscribe_status(collector([]))

        # Reason code 5 is "not authorised": in LAN mode, a wrong access code.
        assert error.value.code == "provider_authentication_failed"
        assert "mqtt connection refused: 5" in error.value.detail

    @pytest.mark.asyncio
    async def test_closes_the_session_after_a_refused_connection(self) -> None:
        wire = ScriptedMqttClient(reason_code=5)
        client = make_client(mqtt_client_factory=lambda: wire)

        with pytest.raises(ProviderError):
            await client.subscribe_status(collector([]))

        assert wire.calls[-2:] == [("disconnect",), ("loop_stop",)]

    @pytest.mark.asyncio
    async def test_surfaces_a_printer_identity_mismatch(self) -> None:
        wire = ScriptedMqttClient(peer_common_name="OTHER-SERIAL")
        client = make_client(mqtt_client_factory=lambda: wire)

        with pytest.raises(ProviderError) as error:
            await client.subscribe_status(collector([]))

        # The live session runs for days; an impersonating LAN device would
        # otherwise receive this printer's access code for all of them.
        assert error.value.code == "provider_authentication_failed"
        assert wire.calls[-2:] == [("disconnect",), ("loop_stop",)]


class TestSubscribeSnapshots:
    @pytest.mark.asyncio
    async def test_delivers_a_typed_snapshot_for_each_report(self) -> None:
        wire = ScriptedMqttClient(messages=[PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)
        received: list[Any] = []

        async def on_snapshot(snapshot: Any) -> None:
            received.append(snapshot)

        await client.subscribe_snapshots(on_snapshot)

        assert [snapshot.state for snapshot in received] == ["printing"]
        assert received[0].progress == 0.5

    @pytest.mark.asyncio
    async def test_passes_the_stop_event_through_to_the_session(self) -> None:
        stop = asyncio.Event()
        wire = ScriptedMqttClient(messages=[PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)
        received: list[Any] = []

        async def on_snapshot(snapshot: Any) -> None:
            received.append(snapshot)
            stop.set()

        await client.subscribe_snapshots(on_snapshot, stop_event=stop)

        # Without the pass-through, a snapshot subscriber could not be stopped.
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_keeps_the_legacy_payload_recoverable_from_the_snapshot(self) -> None:
        wire = ScriptedMqttClient(messages=[PRINTING_REPORT])
        client = make_client(mqtt_client_factory=lambda: wire)
        received: list[Any] = []

        async def on_snapshot(snapshot: Any) -> None:
            received.append(snapshot)

        await client.subscribe_snapshots(on_snapshot)

        # The snapshot round-trips to the exact frame a legacy subscriber would
        # have received — unwrapped here, because that is the shape the status
        # subscription delivers, while `query_status` delivers the envelope.
        # Both callers keep working off one object.
        assert received[0].to_legacy_payload() == {
            "print_stats": {"state": "printing"},
            "virtual_sdcard": {"progress": 0.5},
        }
