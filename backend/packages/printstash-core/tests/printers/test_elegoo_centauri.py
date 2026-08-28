"""The Elegoo Centauri client, across two generations and two pycentauri versions.

Elegoo ships two printers under one name and they do not speak the same protocol:
the Centauri Carbon uses unauthenticated local SDCP, and the Carbon 2 uses
authenticated MQTT with an access code. One client covers both, so which
transport is opened is decided from stored configuration — and getting that wrong
means either a printer that never connects or an access code sent to a device
that does not expect one.

The second axis is `pycentauri` itself. Its `connect` and `start_print`
signatures have gained keyword arguments across releases, and a self-hoster's
environment may hold any of them. `_call_supported_kwargs` inspects the callable
and passes only what it accepts, which is why this file tests old *and* current
signatures rather than pinning one — an unexpected keyword would otherwise crash
every print start on an older install.

The third concern is connection hygiene. The printer grants very few concurrent
connections, and a leaked one leaves it unreachable until it is power-cycled —
which a user experiences as broken hardware, not as a PrintStash bug. So every
path through `_with_connection` closes, including the ones where the action
failed and the one where the *close* itself failed.

Status codes are the last piece: Elegoo's numbers are firmware-specific and
undocumented, so the mapping is pinned code by code, with anything unrecognised
becoming `unknown` rather than a guess.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from pycentauri.client import Printer, PrinterError
from pycentauri.models import Status

import printstash_core.printers.elegoo_centauri as elegoo_module
from printstash_core.printers.contracts import PrinterClient
from printstash_core.printers.elegoo_centauri import (
    ELEGOO_CENTAURI_CAPABILITIES,
    ElegooCentauriClient,
    ElegooCentauriError,
    ElegooCentauriFactory,
    _call_supported_kwargs,
)
from printstash_core.printers.models import (
    ElegooCentauriConfig,
    OctoPrintConfig,
    PrinterSnapshot,
    ProviderError,
)

HOST = "192.168.1.50"
# Obviously fake. A Carbon 2 access code is printed on the printer's screen.
ACCESS_CODE = "ABC123"
MAINBOARD_ID = "mainboard-id"
# 13 is one of the two "printing" codes in Elegoo's undocumented vocabulary.
PRINTING_CODE = 13

PRINTING_ENVELOPE = {
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


def _status(code: int = PRINTING_CODE, **overrides: Any) -> Status:
    payload: dict[str, Any] = {
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
    payload.update(overrides)
    return Status.from_payload(payload)


class FakeConnection:
    """A pycentauri connection that refuses control unless it was enabled.

    The refusal is the interesting part: the real printer rejects a control
    action on a connection opened read-only, and PrintStash opens read-only for
    every status poll. A double that accepted everything would hide a client
    that forgot to ask for control.
    """

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


class ModernConnection(FakeConnection):
    """A pycentauri release whose `start_print` takes the newer options."""

    async def start_print(self, filename: str, **kwargs: Any) -> dict[str, Any]:
        self._require_control("start_print")
        self.calls.append(("start", (filename, kwargs)))
        return {}


def make_client(
    connection: FakeConnection | None = None,
    *,
    model: str = "elegoo_centauri_carbon",
    access_code: str | None = None,
    logger: logging.Logger | None = None,
) -> ElegooCentauriClient:
    """A client whose connector hands back one prepared connection."""

    prepared = connection if connection is not None else FakeConnection()

    async def connector(enable_control: bool) -> FakeConnection:
        prepared.enable_control = prepared.enable_control and enable_control
        return prepared

    return ElegooCentauriClient(
        ElegooCentauriConfig(HOST, model, access_code),
        connector=connector,
        logger=logger,
    )


def recording_client(
    connections: list[FakeConnection], *, modern: bool = False
) -> ElegooCentauriClient:
    """A client that records each connection it opens and how it opened it."""

    async def connector(enable_control: bool) -> FakeConnection:
        factory = ModernConnection if modern else FakeConnection
        connection = factory(enable_control=enable_control)
        connections.append(connection)
        return connection

    return ElegooCentauriClient(
        ElegooCentauriConfig(HOST, "elegoo_centauri_carbon_2", ACCESS_CODE),
        connector=connector,
    )


class TestCallSupportedKwargs:
    @pytest.mark.asyncio
    async def test_passes_a_keyword_the_callable_accepts(self) -> None:
        async def action(host: str, *, enable_control: bool = False) -> object:
            return (host, enable_control)

        assert await _call_supported_kwargs(action, HOST, enable_control=True) == (
            HOST,
            True,
        )

    @pytest.mark.asyncio
    async def test_drops_a_keyword_the_callable_does_not_accept(self) -> None:
        async def old_signature(host: str) -> str:
            return host

        # This is the whole point: a keyword added in a later pycentauri would
        # otherwise raise TypeError on every install that predates it.
        assert (
            await _call_supported_kwargs(old_signature, HOST, mainboard_id=MAINBOARD_ID)
            == HOST
        )

    @pytest.mark.asyncio
    async def test_passes_everything_to_a_callable_taking_arbitrary_keywords(
        self,
    ) -> None:
        async def flexible(host: str, **kwargs: Any) -> dict[str, Any]:
            return kwargs

        assert await _call_supported_kwargs(
            flexible, HOST, storage="local", timelapse=False
        ) == {"storage": "local", "timelapse": False}

    @pytest.mark.asyncio
    async def test_calls_a_callable_it_cannot_introspect(self) -> None:
        # Some C-implemented or wrapped callables have no retrievable signature;
        # refusing to call them would break the client for no benefit.
        assert await _call_supported_kwargs(_uninspectable(), HOST) == HOST


class TestSecondGenerationTransport:
    @pytest.mark.asyncio
    async def test_opens_the_authenticated_transport_with_the_access_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        async def connect(host: str, **kwargs: Any) -> FakeConnection:
            seen["host"] = host
            seen.update(kwargs)
            return FakeConnection()

        monkeypatch.setattr(elegoo_module.CC2Printer, "connect", connect)
        client = ElegooCentauriClient(
            ElegooCentauriConfig(
                HOST, "elegoo_centauri_carbon_2", ACCESS_CODE, mainboard_id=MAINBOARD_ID
            )
        )

        await client.query_status()

        # The Carbon 2 speaks authenticated MQTT, not the first generation's
        # unauthenticated SDCP — connecting to the wrong one never succeeds.
        assert seen == {
            "host": HOST,
            "enable_control": False,
            "access_code": ACCESS_CODE,
            "mainboard_id": MAINBOARD_ID,
        }

    @pytest.mark.asyncio
    async def test_refuses_to_connect_when_the_stored_access_code_is_gone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def connect(*_args: Any, **_kwargs: Any) -> FakeConnection:
            raise AssertionError("must not connect without an access code")

        monkeypatch.setattr(elegoo_module.CC2Printer, "connect", connect)
        client = ElegooCentauriClient(
            ElegooCentauriConfig(HOST, "elegoo_centauri_carbon_2", ACCESS_CODE)
        )
        # A row written before the config-time check existed, or one whose
        # secret failed to decrypt, arrives here with nothing to authenticate
        # with. Connecting anonymously would look like a printer fault.
        client.access_code = None

        with pytest.raises(ElegooCentauriError) as error:
            await client.query_status()

        assert error.value.code == "provider_credentials_missing"


class TestInfo:
    @pytest.mark.asyncio
    async def test_identifies_the_printer_without_opening_a_connection(self) -> None:
        connection = FakeConnection()

        info = await make_client(connection).info()

        # Connections are scarce; an identity read must not spend one.
        assert info == {
            "result": {
                "provider": "elegoo_centauri",
                "model": "elegoo_centauri_carbon",
                "host": HOST,
            }
        }
        assert connection.closed is False


class TestServerInfo:
    @pytest.mark.asyncio
    async def test_answers_with_the_same_payload_as_info(self) -> None:
        client = make_client()

        # The capability is declared supported, so it must answer rather than
        # refuse — there is no separate server endpoint on this printer.
        assert await client.server_info() == await client.info()


class TestQueryStatus:
    @pytest.mark.asyncio
    async def test_normalizes_a_status_into_the_shared_envelope(self) -> None:
        assert await make_client().query_status() == PRINTING_ENVELOPE

    @pytest.mark.asyncio
    async def test_opens_the_connection_read_only(self) -> None:
        connections: list[FakeConnection] = []
        client = recording_client(connections)

        await client.query_status()

        # A status poll that asked for control would take the control channel
        # away from a queued print.
        assert [connection.enable_control for connection in connections] == [False]

    @pytest.mark.asyncio
    async def test_closes_the_connection(self) -> None:
        connection = FakeConnection()

        await make_client(connection).query_status()

        assert connection.closed is True


class TestQuerySnapshot:
    @pytest.mark.asyncio
    async def test_returns_the_same_reading_as_the_legacy_envelope(self) -> None:
        client = make_client()

        legacy = await client.query_status()
        snapshot = await client.query_snapshot()

        assert snapshot == PrinterSnapshot.from_legacy_payload(legacy)


class TestNormalizeStatus:
    @pytest.mark.parametrize(
        ("code", "state"),
        [
            (0, "standby"),
            (1, "standby"),
            (5, "paused"),
            (6, "paused"),
            (7, "cancelled"),
            (8, "cancelled"),
            (9, "complete"),
            (10, "standby"),
            (11, "standby"),
            (12, "printing"),
            (13, "printing"),
            (14, "error"),
            (15, "standby"),
            (16, "standby"),
            (17, "standby"),
            (18, "printing"),
            (27, "paused"),
            (28, "paused"),
            (29, "paused"),
        ],
    )
    def test_translates_every_known_status_code(self, code: int, state: str) -> None:
        # Elegoo's codes are firmware-specific and undocumented, so each one is
        # pinned: a terminal code read as `printing` leaves a finished job
        # running forever and its spool debited.
        normalized = ElegooCentauriClient.normalize_status(_status(code))

        assert normalized["print_stats"]["state"] == state

    def test_reports_an_unrecognised_code_as_unknown(self) -> None:
        normalized = ElegooCentauriClient.normalize_status(_status(999))

        # A firmware update adding a code must not be silently mapped to a
        # state PrintStash would act on.
        assert normalized["print_stats"]["state"] == "unknown"

    def test_reports_standby_when_the_printer_sends_no_status_at_all(self) -> None:
        normalized = ElegooCentauriClient.normalize_status(object())

        assert normalized["print_stats"]["state"] == "standby"

    def test_surfaces_an_error_string_when_there_is_no_message(self) -> None:
        normalized = ElegooCentauriClient.normalize_status(
            _status(14, Message="", Error="Thermal runaway")
        )

        # The error text is the only thing telling the operator what happened.
        assert normalized["print_stats"]["message"] == "Thermal runaway"

    def test_reports_an_empty_message_when_the_printer_sends_neither(self) -> None:
        normalized = ElegooCentauriClient.normalize_status(_status(13, Message=""))

        # Rendered directly in the UI, so `None` would print as "None".
        assert normalized["print_stats"]["message"] == ""

    @pytest.mark.parametrize(
        ("reported", "progress"), [(0, 0.0), (100, 1.0), (-10, 0.0), (140, 1.0)]
    )
    def test_clamps_progress_into_the_unit_range(
        self, reported: float, progress: float
    ) -> None:
        normalized = ElegooCentauriClient.normalize_status(
            _status(13, PrintInfo={"Status": 13, "Progress": reported})
        )

        assert normalized["virtual_sdcard"] == {"progress": progress}

    def test_reports_no_progress_when_the_printer_sends_something_unusable(
        self,
    ) -> None:
        class Unusable:
            progress = "n/a"

        normalized = ElegooCentauriClient.normalize_status(Unusable())

        # A progress bar is not worth a crashed poll loop.
        assert normalized["virtual_sdcard"] == {"progress": 0.0}

    def test_reports_no_duration_when_the_printer_sends_no_print_info(self) -> None:
        normalized = ElegooCentauriClient.normalize_status(object())

        assert normalized["print_stats"]["print_duration"] is None

    def test_reports_no_temperatures_rather_than_raising_when_none_are_sent(
        self,
    ) -> None:
        normalized = ElegooCentauriClient.normalize_status(object())

        assert normalized["heater_bed"] == {"temperature": None, "target": None}


class TestUpload:
    @pytest.mark.asyncio
    async def test_returns_the_remote_name_the_printer_stored(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")

        result = await recording_client([]).upload(source, "cube.gcode")

        assert result == {"result": "cube.gcode"}

    @pytest.mark.asyncio
    async def test_opens_the_connection_with_control_enabled(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        connections: list[FakeConnection] = []

        await recording_client(connections).upload(source, "cube.gcode")

        # The printer rejects an upload on a read-only connection.
        assert [connection.calls[0][0] for connection in connections] == ["upload"]
        assert all(connection.enable_control for connection in connections)


class TestStart:
    @pytest.mark.asyncio
    async def test_starts_a_print_on_a_pycentauri_release_with_no_options(
        self,
    ) -> None:
        connections: list[FakeConnection] = []

        assert await recording_client(connections).start("cube.gcode") == {"ok": True}
        # The old signature takes only the filename; passing the newer options
        # would raise TypeError on this install.
        assert connections[0].calls == [("start", "cube.gcode")]

    @pytest.mark.asyncio
    async def test_sends_the_print_options_a_newer_release_accepts(self) -> None:
        connections: list[FakeConnection] = []

        await recording_client(connections, modern=True).start("cube.gcode")

        # `auto_leveling` and `timelapse` are the defaults PrintStash wants, and
        # `storage="local"` names the file it just uploaded rather than a USB
        # stick that may hold a different file of the same name.
        assert connections[0].calls == [
            (
                "start",
                (
                    "cube.gcode",
                    {"storage": "local", "auto_leveling": True, "timelapse": False},
                ),
            )
        ]


class TestPause:
    @pytest.mark.asyncio
    async def test_pauses_over_a_control_connection(self) -> None:
        connections: list[FakeConnection] = []

        assert await recording_client(connections).pause() == {"ok": True}
        assert connections[0].calls == [("pause", None)]


class TestResume:
    @pytest.mark.asyncio
    async def test_resumes_over_a_control_connection(self) -> None:
        connections: list[FakeConnection] = []

        assert await recording_client(connections).resume() == {"ok": True}
        assert connections[0].calls == [("resume", None)]


class TestCancel:
    @pytest.mark.asyncio
    async def test_sends_stop_rather_than_cancel(self) -> None:
        connections: list[FakeConnection] = []

        assert await recording_client(connections).cancel() == {"ok": True}
        # There is no cancel in this protocol; `stop` is it.
        assert connections[0].calls == [("stop", None)]


class TestCloseQuietly:
    @pytest.mark.asyncio
    async def test_logs_a_close_failure_instead_of_raising(self) -> None:
        class UncloseableConnection(FakeConnection):
            async def close(self) -> None:
                raise RuntimeError("socket already gone")

        logger = logging.getLogger("test.elegoo.close")
        records: list[str] = []
        logger.addHandler(_Collector(records))
        logger.setLevel(logging.DEBUG)

        await make_client(UncloseableConnection(), logger=logger).query_status()

        # Debug, not a warning: a close that fails after a successful read is
        # not the operator's problem, and the read's result still stands.
        assert any("close failed" in record for record in records)


class TestSubscribeStatus:
    @pytest.mark.asyncio
    async def test_delivers_the_watched_status(self) -> None:
        stop = asyncio.Event()
        received: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            received.append(status)
            stop.set()

        await make_client(FakeConnection(_status(6))).subscribe_status(
            on_status, stop_event=stop
        )

        assert [status["print_stats"]["state"] for status in received] == ["paused"]

    @pytest.mark.asyncio
    async def test_closes_the_connection_when_the_watch_ends(self) -> None:
        stop = asyncio.Event()
        connection = FakeConnection()

        async def on_status(_status: dict[str, Any]) -> None:
            stop.set()

        await make_client(connection).subscribe_status(on_status, stop_event=stop)

        assert connection.closed is True

    @pytest.mark.asyncio
    async def test_reports_a_printer_error_raised_by_the_watch(self) -> None:
        class RefusingConnection(FakeConnection):
            async def watch(self):
                raise PrinterError("watch rejected")
                yield  # pragma: no cover - generator marker

        connection = RefusingConnection()
        client = make_client(connection)

        with pytest.raises(ElegooCentauriError):
            await client.subscribe_status(_ignore)

        assert connection.closed is True

    @pytest.mark.asyncio
    async def test_reports_a_network_drop_during_the_watch(self) -> None:
        class DroppingConnection(FakeConnection):
            async def watch(self):
                raise OSError("connection reset")
                yield  # pragma: no cover - generator marker

        connection = DroppingConnection()
        client = make_client(connection)

        with pytest.raises(ElegooCentauriError):
            await client.subscribe_status(_ignore)

        assert connection.closed is True


class TestSubscribeSnapshots:
    @pytest.mark.asyncio
    async def test_delivers_a_typed_snapshot(self) -> None:
        stop = asyncio.Event()
        received: list[PrinterSnapshot] = []

        async def on_snapshot(snapshot: PrinterSnapshot) -> None:
            received.append(snapshot)
            stop.set()

        await make_client(FakeConnection(_status(6))).subscribe_snapshots(
            on_snapshot, stop_event=stop
        )

        assert [snapshot.state for snapshot in received] == ["paused"]


class TestCapabilities:
    def test_declares_itself_beta(self) -> None:
        # `docs/provider-support.md` publishes this level and the UI shows it.
        assert ELEGOO_CENTAURI_CAPABILITIES.support_level == "beta"

    def test_satisfies_the_neutral_printer_protocol(self) -> None:
        client = ElegooCentauriFactory().build(
            ElegooCentauriConfig(HOST, "elegoo_centauri_carbon")
        )

        assert isinstance(client, PrinterClient)


class TestElegooCentauriFactory:
    def test_builds_an_elegoo_client(self) -> None:
        config = ElegooCentauriConfig(HOST, "elegoo_centauri_carbon")

        client = ElegooCentauriFactory().build(config)

        assert isinstance(client, ElegooCentauriClient)
        assert client.config is config

    def test_passes_injected_dependencies_through_to_the_client(self) -> None:
        logger = logging.getLogger("test.elegoo.factory")

        async def connector(_enable_control: bool) -> FakeConnection:
            return FakeConnection()

        client = ElegooCentauriFactory(connector=connector, logger=logger).build(
            ElegooCentauriConfig(HOST, "elegoo_centauri_carbon")
        )

        assert client._connector is connector
        assert client._logger is logger

    def test_advertises_the_capabilities_before_building_a_client(self) -> None:
        assert ElegooCentauriFactory().capabilities is ELEGOO_CENTAURI_CAPABILITIES

    def test_refuses_a_configuration_for_another_provider(self) -> None:
        with pytest.raises(ProviderError) as error:
            ElegooCentauriFactory().build(
                OctoPrintConfig("http://octoprint.local", "key")
            )

        assert error.value.code == "provider_config_mismatch"


class _Collector(logging.Handler):
    def __init__(self, into: list[str]) -> None:
        super().__init__()
        self._into = into

    def emit(self, record: logging.LogRecord) -> None:
        self._into.append(record.getMessage())


def _uninspectable() -> Any:
    """A callable whose signature cannot be retrieved."""

    class Uninspectable:
        # `inspect.signature` raises TypeError when this attribute is not a
        # Signature, which is how a wrapped or C-implemented callable reaches
        # the fall-through branch.
        __signature__ = "not a signature"

        async def __call__(self, host: str) -> str:
            return host

    return Uninspectable()


async def _ignore(_status: dict[str, Any]) -> None:
    return None


class TestConnect:
    """The real `_connect`, which is where credentials and errors are decided."""

    def test_second_generation_requires_an_access_code(self) -> None:
        from printstash_core.printers.models import ProviderError

        # Refused when the *config* is built, not when the connection is opened —
        # which is earlier and better: the failure lands in the settings form
        # rather than mid-queue, and no connection is spent discovering it.
        with pytest.raises(ProviderError) as caught:
            ElegooCentauriConfig(
                host="printer.invalid",
                model="elegoo_centauri_carbon_2",
                access_code=None,
                mainboard_id=None,
            )

        assert "credentials_missing" in str(caught.value)

    @pytest.mark.asyncio
    async def test_maps_an_auth_shaped_printer_error_to_an_auth_code(
        self, monkeypatch
    ) -> None:
        async def refuse(*_args: object, **_kwargs: object):
            raise PrinterError("access code rejected")

        monkeypatch.setattr(Printer, "connect", staticmethod(refuse))
        client = ElegooCentauriClient(
            ElegooCentauriConfig(
                host="printer.invalid",
                model="elegoo_centauri_carbon",
                access_code=None,
                mainboard_id=None,
            )
        )

        # From the user's side a wrong access code and a refusing printer are the
        # same thing to fix, so both surface as an authentication failure — which
        # is the code that prompts for credentials exactly once.
        with pytest.raises(ElegooCentauriError) as caught:
            await client.query_status()

        assert caught.value.code == "provider_authentication_failed"

    @pytest.mark.asyncio
    async def test_maps_any_other_printer_error_to_a_transport_code(
        self, monkeypatch
    ) -> None:
        async def refuse(*_args: object, **_kwargs: object):
            raise PrinterError("mainboard busy")

        monkeypatch.setattr(Printer, "connect", staticmethod(refuse))
        client = ElegooCentauriClient(
            ElegooCentauriConfig(
                host="printer.invalid",
                model="elegoo_centauri_carbon",
                access_code=None,
                mainboard_id=None,
            )
        )

        # Not an auth problem: prompting for credentials here would send the user
        # to fix something that is already correct.
        with pytest.raises(ElegooCentauriError) as caught:
            await client.query_status()

        assert caught.value.code == "provider_transport_error"

    @pytest.mark.asyncio
    async def test_reports_a_network_failure_as_a_provider_error(
        self, monkeypatch
    ) -> None:
        async def unreachable(*_args: object, **_kwargs: object):
            raise OSError("no route to host")

        monkeypatch.setattr(Printer, "connect", staticmethod(unreachable))
        client = ElegooCentauriClient(
            ElegooCentauriConfig(
                host="printer.invalid",
                model="elegoo_centauri_carbon",
                access_code=None,
                mainboard_id=None,
            )
        )

        with pytest.raises(ElegooCentauriError):
            await client.query_status()

    @pytest.mark.asyncio
    async def test_reports_a_timeout_as_a_provider_error(self, monkeypatch) -> None:
        async def too_slow(*_args: object, **_kwargs: object):
            raise asyncio.TimeoutError

        monkeypatch.setattr(Printer, "connect", staticmethod(too_slow))
        client = ElegooCentauriClient(
            ElegooCentauriConfig(
                host="printer.invalid",
                model="elegoo_centauri_carbon",
                access_code=None,
                mainboard_id=None,
            )
        )

        with pytest.raises(ElegooCentauriError):
            await client.query_status()


class TestWithConnection:
    """Whatever happens, the connection closes — the printer grants few of them."""

    def _client(self, connection) -> ElegooCentauriClient:
        async def connector(_enable_control: bool):
            return connection

        return ElegooCentauriClient(
            ElegooCentauriConfig(
                host="printer.invalid",
                model="elegoo_centauri_carbon",
                access_code=None,
                mainboard_id=None,
            ),
            connector=connector,
        )

    @pytest.mark.asyncio
    async def test_closes_the_connection_after_a_read(self) -> None:
        connection = FakeConnection()

        await self._client(connection).query_status()

        # Leaking a connection leaves the printer unreachable until it is
        # power-cycled, which the user experiences as broken hardware.
        assert connection.closed is True

    @pytest.mark.asyncio
    async def test_closes_the_connection_after_a_failed_action(self) -> None:
        connection = FakeConnection(enable_control=False)
        client = self._client(connection)

        with pytest.raises(ElegooCentauriError):
            await client.pause()

        assert connection.closed is True

    @pytest.mark.asyncio
    async def test_swallows_a_failure_raised_by_the_close_itself(self) -> None:
        class UncloseableConnection(FakeConnection):
            async def close(self) -> None:
                raise RuntimeError("socket already gone")

        connection = UncloseableConnection()

        # A close error must not mask the result of an operation that already
        # succeeded — the caller has a valid status either way.
        snapshot = await self._client(connection).query_snapshot()

        assert isinstance(snapshot, PrinterSnapshot)

    @pytest.mark.asyncio
    async def test_reports_a_network_drop_mid_action(self) -> None:
        class DroppingConnection(FakeConnection):
            async def pause(self) -> dict[str, Any]:
                raise OSError("connection reset")

        connection = DroppingConnection()
        client = self._client(connection)

        with pytest.raises(ElegooCentauriError):
            await client.pause()

        assert connection.closed is True


class TestWithConnectionErrorPassThrough:
    @pytest.mark.asyncio
    async def test_does_not_reclassify_an_error_the_action_already_named(
        self,
    ) -> None:
        class RefusingConnection(FakeConnection):
            async def status(self) -> Status:
                raise ElegooCentauriError(
                    "already classified", code="provider_credentials_missing"
                )

        connection = RefusingConnection()
        client = make_client(connection)

        with pytest.raises(ElegooCentauriError) as error:
            await client.query_status()

        # Wrapping it again would flatten a specific cause into a generic
        # transport error and lose the prompt it was meant to trigger.
        assert error.value.code == "provider_credentials_missing"
        assert connection.closed is True


class TestUnsupportedActions:
    """A beta provider says what it cannot do rather than failing obscurely."""

    def _client(self) -> ElegooCentauriClient:
        async def connector(_enable_control: bool):
            return FakeConnection()

        return ElegooCentauriClient(
            ElegooCentauriConfig(
                host="printer.invalid",
                model="elegoo_centauri_carbon",
                access_code=None,
                mainboard_id=None,
            ),
            connector=connector,
        )

    @pytest.mark.parametrize(
        "action",
        [
            pytest.param("list_files", id="list-files"),
            pytest.param("delete_file", id="delete-file"),
            pytest.param("run_gcode", id="run-gcode"),
            pytest.param("emergency_stop", id="emergency-stop"),
            pytest.param("server_config", id="server-config"),
            pytest.param("printer_config", id="printer-config"),
        ],
    )
    @pytest.mark.asyncio
    async def test_refuses_an_action_the_printer_does_not_expose(
        self, action: str
    ) -> None:
        client = self._client()
        target = getattr(client, action)

        # The capability block already tells the UI to hide these, so reaching
        # one means something bypassed it — which must be an explicit refusal,
        # not a silent no-op that looks like success.
        takes_argument = action in {"delete_file", "run_gcode"}

        with pytest.raises(ElegooCentauriError):
            await (target("x") if takes_argument else target())
