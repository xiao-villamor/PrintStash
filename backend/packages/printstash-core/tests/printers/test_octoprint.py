"""The OctoPrint REST client: state derivation, path safety, and error codes.

OctoPrint is PrintStash's *stable* provider — the one a self-hoster is most
likely to already be running — so this client's contract is the most load-bearing
of the four. Three things earn their tests here.

**State is derived, not reported.** OctoPrint publishes a bag of booleans
(`printing`, `pausing`, `cancelling`, `closedOrError`) plus a completion
percentage, and PrintStash has to turn that into one state the job machine
understands. The subtle case is 100% *with* `printing` still set: that is a print
about to end, not one that ended, and reading the percentage alone closes the job
record while the nozzle is still moving.

**Remote filenames become URL path segments.** A name from the library is quoted
per segment and refused if it could climb out of the upload root — the file being
addressed lives on the printer's own storage, next to everyone else's.

**Error codes are branched on.** `409` is not a fault: it means there is no
active job. `401` and `403` share a code because both mean "fix your
credentials", and that code is what triggers one prompt rather than a retry loop.
A code drifting here changes what the UI tells the operator to do.

Uploads stream from an open file rather than reading it whole, because a G-code
file for a large print is routinely hundreds of megabytes and this runs in the
API process. There is a test that fails if `read_bytes` is ever reintroduced.

Wire-level behaviour against the real OctoPrint emulator lives in the backend's
contract tier; everything here is the client's own logic over a mock transport.
"""

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
from printstash_core.printers.octoprint import (
    OCTOPRINT_CAPABILITIES,
    OctoPrintClient,
    OctoPrintError,
    OctoPrintFactory,
)

API_KEY = "key-123"

PRINTING_PRINTER = {
    "state": {"text": "Printing", "flags": {"printing": True}},
    "temperature": {
        "bed": {"actual": 59.5, "target": 60},
        "tool0": {"actual": 214, "target": 215},
    },
}
PRINTING_JOB = {
    "job": {"file": {"name": "cube.gcode"}},
    "progress": {"completion": 25.0, "printTime": 120},
}
PRINTING_ENVELOPE = {
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


def _client(handler: Any) -> OctoPrintClient:
    return OctoPrintClient(
        OctoPrintConfig("http://octopi.local/", API_KEY),
        transport=httpx.MockTransport(handler),
    )


def printing_client() -> OctoPrintClient:
    """A client whose printer reports a print in progress."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/printer":
            return httpx.Response(200, json=PRINTING_PRINTER)
        return httpx.Response(200, json=PRINTING_JOB)

    return _client(handler)


def recording(seen: list[tuple[str, str]], **by_path: Any) -> Any:
    """A handler that records every (method, path) before answering."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        body = by_path.get(request.url.path)
        return httpx.Response(200, json=body if body is not None else {})

    return handler


def listing(files: Any) -> Any:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"files": files})

    return handler


class TestClientSetup:
    @pytest.mark.asyncio
    async def test_authenticates_with_the_configured_api_key(self) -> None:
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("X-Api-Key"))
            return httpx.Response(200, json={})

        await _client(handler).info()

        assert seen == [API_KEY]

    def test_strips_a_trailing_slash_from_the_configured_url(self) -> None:
        # Every path this client builds starts with `/`, so a retained slash
        # produces `//api/...`, which OctoPrint 404s.
        assert _client(lambda _r: httpx.Response(204)).base_url == (
            "http://octopi.local"
        )


class TestInfo:
    @pytest.mark.asyncio
    async def test_reports_the_printers_version_under_a_provider_envelope(
        self,
    ) -> None:
        client = _client(lambda _r: httpx.Response(200, json={"server": "1.9.3"}))

        assert await client.info() == {
            "result": {"provider": "octoprint", "version": {"server": "1.9.3"}}
        }


class TestServerInfo:
    @pytest.mark.asyncio
    async def test_answers_with_the_same_payload_as_info(self) -> None:
        client = _client(lambda _r: httpx.Response(200, json={"server": "1.9.3"}))

        # OctoPrint has no separate server endpoint, and the capability is
        # declared supported, so it must answer rather than refuse.
        assert await client.server_info() == await client.info()


class TestQueryStatus:
    @pytest.mark.asyncio
    async def test_normalizes_a_printing_status_into_the_shared_envelope(self) -> None:
        assert await printing_client().query_status() == PRINTING_ENVELOPE

    @pytest.mark.asyncio
    async def test_reports_standby_when_the_printer_is_disconnected(self) -> None:
        # OctoPrint 404s both `/api/printer` and `/api/job` while no printer is
        # connected to it. That is an idle OctoPi, not an error, so the status
        # poll has to degrade rather than mark the device unreachable.
        client = _client(lambda _r: httpx.Response(404))

        status = await client.query_status()

        assert status["result"]["status"]["print_stats"]["state"] == "standby"


class TestQuerySnapshot:
    @pytest.mark.asyncio
    async def test_returns_the_same_reading_as_the_legacy_envelope(self) -> None:
        client = printing_client()

        legacy = await client.query_status()
        snapshot = await client.query_snapshot()

        # Both shapes are live: the typed snapshot for new code, the envelope
        # for the existing API surface.
        assert snapshot == PrinterSnapshot.from_legacy_payload(legacy)


class TestNormalizeStatus:
    def test_falls_back_to_the_files_path_when_it_has_no_name(self) -> None:
        status = OctoPrintClient._normalize_status(
            {}, {"job": {"file": {"path": "folder/cube.gcode"}}}
        )

        assert status["print_stats"]["filename"] == "folder/cube.gcode"

    def test_reports_an_empty_message_when_the_printer_sends_no_text(self) -> None:
        status = OctoPrintClient._normalize_status({}, {})

        # The message is rendered directly; `None` would print as "None".
        assert status["print_stats"]["message"] == ""

    @pytest.mark.parametrize(
        ("completion", "progress"), [(0, 0.0), (100, 1.0), (-5, 0.0), (140, 1.0)]
    )
    def test_clamps_progress_into_the_unit_range(
        self, completion: float, progress: float
    ) -> None:
        status = OctoPrintClient._normalize_status(
            {}, {"progress": {"completion": completion}}
        )

        assert status["virtual_sdcard"] == {"progress": progress}

    def test_reports_no_temperatures_rather_than_raising_when_none_are_sent(
        self,
    ) -> None:
        status = OctoPrintClient._normalize_status({"temperature": {}}, {})

        assert status["heater_bed"] == {"temperature": None, "target": None}

    @pytest.mark.parametrize(
        "printer",
        [
            {"temperature": "unavailable"},
            {"temperature": {"bed": "unavailable"}},
            {"temperature": {"tool0": "unavailable"}},
        ],
    )
    def test_survives_a_temperature_block_of_the_wrong_shape(
        self, printer: dict[str, Any]
    ) -> None:
        # A plugin can replace this block wholesale; the poll loop must not die
        # for as long as it keeps doing so.
        status = OctoPrintClient._normalize_status(printer, {})

        assert status["extruder"] == {"temperature": None, "target": None}


class TestFlattenFiles:
    def test_lists_a_file_at_the_top_level(self) -> None:
        files = OctoPrintClient._flatten_files(
            [{"name": "cube.gcode", "path": "cube.gcode", "type": "machinecode"}]
        )

        assert [item["path"] for item in files] == ["cube.gcode"]

    def test_descends_into_a_folder(self) -> None:
        files = OctoPrintClient._flatten_files(
            [
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
        )

        # The path is what `start` and `delete_file` are called with later, so a
        # bare filename here would address the wrong file.
        assert [item["path"] for item in files] == ["folder/cube.gcode"]

    def test_reports_file_size_alongside_modification_time(self) -> None:
        files = OctoPrintClient._flatten_files(
            [{"name": "a.gcode", "type": "machinecode", "size": 2048, "date": 1700}]
        )

        assert files == [
            {
                "path": "a.gcode",
                "filename": "a.gcode",
                "size": 2048,
                "modified": 1700,
            }
        ]

    def test_lists_an_entry_that_declares_no_type(self) -> None:
        # Older OctoPrint versions omit it for plain uploads.
        files = OctoPrintClient._flatten_files([{"name": "a.gcode"}])

        assert [item["filename"] for item in files] == ["a.gcode"]

    @pytest.mark.parametrize("item_type", ["model", "folder-ish", "sdcard"])
    def test_skips_an_entry_that_is_not_printable(self, item_type: str) -> None:
        # An STL stored on the printer is not something PrintStash can start.
        files = OctoPrintClient._flatten_files([{"name": "a.stl", "type": item_type}])

        assert files == []

    def test_skips_an_entry_that_is_not_an_object(self) -> None:
        assert OctoPrintClient._flatten_files(["a.gcode"]) == []

    def test_skips_a_folder_whose_children_are_the_wrong_shape(self) -> None:
        files = OctoPrintClient._flatten_files(
            [{"name": "f", "type": "folder", "children": "broken"}]
        )

        assert files == []


class TestListFiles:
    @pytest.mark.asyncio
    async def test_asks_for_a_recursive_listing(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"files": []})

        await _client(handler).list_files()

        # Without `recursive=true`, files inside folders are invisible.
        assert seen == ["http://octopi.local/api/files?recursive=true"]

    @pytest.mark.asyncio
    async def test_returns_nothing_for_an_empty_listing(self) -> None:
        assert await _client(listing([])).list_files() == []

    @pytest.mark.asyncio
    async def test_returns_nothing_when_the_listing_is_the_wrong_shape(self) -> None:
        assert await _client(listing("unavailable")).list_files() == []

    @pytest.mark.asyncio
    async def test_reads_a_listing_returned_as_a_bare_array(self) -> None:
        client = _client(lambda _r: httpx.Response(200, json=[{"name": "a.gcode"}]))

        assert [item["filename"] for item in await client.list_files()] == ["a.gcode"]


class TestUpload:
    @pytest.mark.asyncio
    async def test_streams_the_file_rather_than_reading_it_whole(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n" * 100)

        def forbid_read_bytes(_path: Path) -> bytes:
            raise AssertionError("upload must stream from an open file")

        # A G-code file for a large print is routinely hundreds of megabytes,
        # and this runs in the API process.
        monkeypatch.setattr(Path, "read_bytes", forbid_read_bytes)

        await _client(lambda _r: httpx.Response(200, json={})).upload(
            source, "cube.gcode"
        )

    @pytest.mark.asyncio
    async def test_sends_the_parent_directory_as_a_form_field(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        bodies: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content)
            return httpx.Response(200, json={})

        await _client(handler).upload(source, "sub/dir/cube.gcode")

        # OctoPrint takes the directory as a `path` part, not in the URL.
        assert b'name="path"' in bodies[0]
        assert b"sub/dir" in bodies[0]

    @pytest.mark.asyncio
    async def test_asks_the_printer_not_to_select_or_print_on_arrival(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        bodies: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content)
            return httpx.Response(200, json={})

        await _client(handler).upload(source, "cube.gcode")

        # Otherwise an upload starts printing immediately, bypassing the queue.
        assert b'name="print"\r\n\r\nfalse' in bodies[0]
        assert b'name="select"\r\n\r\nfalse' in bodies[0]

    @pytest.mark.asyncio
    async def test_returns_the_printers_own_response_body(self, tmp_path: Path) -> None:
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        client = _client(lambda _r: httpx.Response(201, json={"done": True}))

        assert await client.upload(source, "cube.gcode") == {"done": True}

    @pytest.mark.asyncio
    async def test_reports_an_ok_marker_when_the_printer_answers_with_an_array(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        client = _client(lambda _r: httpx.Response(200, json=[]))

        assert await client.upload(source, "cube.gcode") == {"ok": True}

    @pytest.mark.asyncio
    async def test_refuses_a_traversing_remote_name(self, tmp_path: Path) -> None:
        client = _client(lambda _r: httpx.Response(200, json={}))

        with pytest.raises(OctoPrintError) as error:
            await client.upload(tmp_path / "missing.gcode", "../cube.gcode")

        assert error.value.code == "provider_error"


class TestStart:
    @pytest.mark.asyncio
    async def test_start_selects_the_file_with_print_set(self) -> None:
        bodies: list[bytes] = []
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            bodies.append(request.content)
            return httpx.Response(204)

        await _client(handler).start("sub/dir/cube.gcode")

        assert paths == ["/api/files/local/sub/dir/cube.gcode"]
        assert b'"command":"select"' in bodies[0].replace(b" ", b"")
        assert b'"print":true' in bodies[0].replace(b" ", b"")


class TestDeleteFile:
    @pytest.mark.asyncio
    async def test_deletes_the_files_own_url(self) -> None:
        seen: list[tuple[str, str]] = []

        await _client(recording(seen)).delete_file("sub/dir/cube.gcode")

        assert seen == [("DELETE", "/api/files/local/sub/dir/cube.gcode")]


class TestPause:
    @pytest.mark.asyncio
    async def test_sends_the_pause_action(self) -> None:
        bodies: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content.replace(b" ", b""))
            return httpx.Response(204)

        await _client(handler).pause()

        # `pause` is one command with an explicit action; sending it without the
        # action toggles, which resumes a paused print.
        assert b'"command":"pause"' in bodies[0]
        assert b'"action":"pause"' in bodies[0]


class TestResume:
    @pytest.mark.asyncio
    async def test_sends_the_resume_action_of_the_pause_command(self) -> None:
        bodies: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content.replace(b" ", b""))
            return httpx.Response(204)

        await _client(handler).resume()

        assert b'"command":"pause"' in bodies[0]
        assert b'"action":"resume"' in bodies[0]


class TestCancel:
    @pytest.mark.asyncio
    async def test_sends_the_cancel_command(self) -> None:
        bodies: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content.replace(b" ", b""))
            return httpx.Response(204)

        await _client(handler).cancel()

        assert b'"command":"cancel"' in bodies[0]

    @pytest.mark.asyncio
    async def test_treats_a_no_content_answer_as_success(self) -> None:
        assert await _client(lambda _r: httpx.Response(204)).cancel() == {"ok": True}


class TestUnsupportedOperations:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "arguments"),
        [
            ("run_gcode", ("G28",)),
            ("emergency_stop", ()),
            ("server_config", ()),
            ("printer_config", ()),
        ],
    )
    async def test_refuses_before_opening_a_connection(
        self, method: str, arguments: tuple[Any, ...]
    ) -> None:
        seen: list[tuple[str, str]] = []
        client = _client(recording(seen))

        with pytest.raises(OctoPrintError) as error:
            await getattr(client, method)(*arguments)

        assert error.value.code == "operation_not_supported_for_provider"
        assert seen == []


class TestSubscribeStatus:
    @pytest.mark.asyncio
    async def test_delivers_the_current_status_once(self) -> None:
        stop = asyncio.Event()
        stop.set()
        received: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            received.append(status)

        await printing_client().subscribe_status(on_status, stop_event=stop)

        # OctoPrint has no push channel, so "subscribing" is one poll and the
        # hub loops. The payload handed up is the status object, not the
        # envelope.
        assert received == [PRINTING_ENVELOPE["result"]["status"]]

    @pytest.mark.asyncio
    async def test_returns_after_one_poll_when_no_stop_event_is_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept: list[float] = []

        async def record(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", record)
        received: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            received.append(status)

        await printing_client().subscribe_status(on_status)

        # A caller with no stop event gets one reading, then the client paces
        # itself before returning so the hub's loop cannot become a busy poll of
        # the printer. The sleep is stubbed because the alternative is a test
        # that takes two real seconds to prove a constant.
        assert len(received) == 1
        assert slept == [2.0]

    @pytest.mark.asyncio
    async def test_returns_when_the_stop_event_never_arrives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def timeout(awaitable: Any, timeout: float) -> None:
            awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", timeout)
        received: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            received.append(status)

        await printing_client().subscribe_status(on_status, stop_event=asyncio.Event())

        # The wait is bounded, so a stop event that is never set returns rather
        # than parking the task forever — otherwise a printer removed from the
        # hub leaks its subscriber. `wait_for` is stubbed for the same reason as
        # above: the real bound is two seconds of wall clock.
        assert len(received) == 1


class TestSubscribeSnapshots:
    @pytest.mark.asyncio
    async def test_delivers_a_typed_snapshot(self) -> None:
        stop = asyncio.Event()
        stop.set()
        received: list[PrinterSnapshot] = []

        async def on_snapshot(snapshot: PrinterSnapshot) -> None:
            received.append(snapshot)

        await printing_client().subscribe_snapshots(on_snapshot, stop_event=stop)

        assert [snapshot.state for snapshot in received] == ["printing"]


class TestCapabilities:
    def test_declares_itself_beta(self) -> None:
        # `docs/provider-support.md` publishes this level and the UI shows it,
        # so the two must not drift: the doc says beta because physical-printer
        # coverage is still expanding, and the UI's "supervise first prints"
        # warning is driven from here.
        assert OCTOPRINT_CAPABILITIES.support_level == "beta"

    def test_satisfies_the_neutral_printer_protocol(self) -> None:
        client = OctoPrintFactory().build(
            OctoPrintConfig("http://octoprint.local", "key")
        )

        assert isinstance(client, PrinterClient)


class TestOctoPrintFactory:
    def test_builds_an_octoprint_client(self) -> None:
        config = OctoPrintConfig("http://octoprint.local", "key")

        client = OctoPrintFactory().build(config)

        assert isinstance(client, OctoPrintClient)
        assert client.config is config

    def test_passes_injected_transport_settings_through_to_the_client(self) -> None:
        transport = httpx.MockTransport(lambda _r: httpx.Response(204))

        client = OctoPrintFactory(timeout=3.0, transport=transport).build(
            OctoPrintConfig("http://octoprint.local", "key")
        )

        assert client.transport is transport
        assert client.timeout == 3.0

    def test_advertises_the_capabilities_before_building_a_client(self) -> None:
        assert OctoPrintFactory().capabilities is OCTOPRINT_CAPABILITIES

    def test_refuses_a_configuration_for_another_provider(self) -> None:
        with pytest.raises(ProviderError) as error:
            OctoPrintFactory().build(
                PrusaLinkConfig("http://prusa.local", "api_key", api_key="key")
            )

        # Provider id and config are separate columns; a mismatch is a
        # configuration error, not an AttributeError on the first poll.
        assert error.value.code == "provider_config_mismatch"


class TestRequestErrorMapping:
    """Every failure gets a stable code, because callers branch on it."""

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            pytest.param(401, "provider_authentication_failed", id="unauthorized"),
            pytest.param(403, "provider_authentication_failed", id="forbidden"),
            pytest.param(404, "provider_endpoint_not_supported", id="not-found"),
            pytest.param(409, "provider_no_active_job", id="conflict"),
            pytest.param(500, "provider_transport_error", id="server-error"),
            pytest.param(418, "provider_transport_error", id="unexpected-4xx"),
        ],
    )
    @pytest.mark.asyncio
    async def test_maps_a_status_to_its_code(self, status: int, code: str) -> None:
        client = _client(lambda _request: httpx.Response(status))

        # `401`/`403` share a code because both mean "fix your credentials", and
        # that code is what triggers exactly one prompt rather than a retry loop.
        # `409` is deliberately *not* a fault: it means there is no active job.
        with pytest.raises(OctoPrintError) as caught:
            await client.info()

        assert caught.value.code == code

    @pytest.mark.asyncio
    async def test_reports_a_timeout_as_its_own_code(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("too slow")

        client = _client(handler)

        # A printer that is slow is a different problem from one that refuses,
        # and the UI says different things about them.
        with pytest.raises(OctoPrintError) as caught:
            await client.info()

        assert caught.value.code == "provider_timeout"

    @pytest.mark.asyncio
    async def test_reports_a_transport_failure(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = _client(handler)

        with pytest.raises(OctoPrintError):
            await client.info()

    @pytest.mark.asyncio
    async def test_treats_an_empty_body_as_success(self) -> None:
        client = _client(lambda _request: httpx.Response(204))

        # OctoPrint answers several actions with 204. That is a success, and
        # turning it into an error would fail every command that worked.
        assert await client.info() == {
            "result": {"provider": "octoprint", "version": {"ok": True}}
        }

    @pytest.mark.asyncio
    async def test_reports_a_body_that_is_not_json(self) -> None:
        client = _client(
            lambda _request: httpx.Response(200, text="<html>proxy</html>")
        )

        # A reverse proxy in front of the printer answers with HTML when it is
        # unhappy; that must not surface as a parser traceback.
        with pytest.raises(OctoPrintError) as caught:
            await client.info()

        assert caught.value.code == "provider_invalid_response"


class TestFilePath:
    @pytest.mark.parametrize(
        "remote_filename",
        [
            pytest.param("/absolute.gcode", id="absolute"),
            pytest.param("../escape.gcode", id="traversal"),
            pytest.param("a/../b.gcode", id="traversal-mid-path"),
            pytest.param("", id="empty"),
        ],
    )
    def test_refuses_a_name_that_could_escape_the_upload_root(
        self, remote_filename: str
    ) -> None:
        # The name reaches the printer as a URL path. A traversal here targets
        # somebody else's file on the printer's own storage.
        with pytest.raises(OctoPrintError) as caught:
            OctoPrintClient._file_path(remote_filename)

        assert caught.value.code == "provider_error"

    def test_encodes_each_segment_of_an_ordinary_name(self) -> None:
        # Per-segment: the slash stays a separator and the space does not turn
        # the filename into two directories.
        assert (
            OctoPrintClient._file_path("folder/my part.gcode")
            == "folder/my%20part.gcode"
        )

    @pytest.mark.parametrize(
        ("remote_filename", "expected"),
        [
            pytest.param("./here.gcode", "here.gcode", id="leading-dot-segment"),
            pytest.param("a//b.gcode", "a/b.gcode", id="doubled-separator"),
        ],
    )
    def test_normalises_a_redundant_segment_rather_than_refusing_it(
        self, remote_filename: str, expected: str
    ) -> None:
        # `PurePosixPath` collapses these before the guard sees them, so they are
        # accepted in their normalised form. That is the safe outcome — neither
        # can escape the root — and refusing them would reject filenames real
        # slicers and users produce.
        assert OctoPrintClient._file_path(remote_filename) == expected


class TestStatusStateMapping:
    @pytest.mark.parametrize(
        ("flags", "completion", "expected"),
        [
            pytest.param({"printing": True}, 12.0, "printing", id="printing"),
            pytest.param({"paused": True}, 40.0, "paused", id="paused"),
            pytest.param({"pausing": True}, 40.0, "paused", id="pausing"),
            pytest.param({"cancelling": True}, 40.0, "cancelled", id="cancelling"),
            pytest.param({"error": True}, 0.0, "error", id="error"),
            pytest.param({"closedOrError": True}, 0.0, "error", id="closed-or-error"),
            pytest.param({}, 100.0, "complete", id="complete-at-100"),
            pytest.param({}, 99.9, "complete", id="complete-at-boundary"),
            pytest.param({}, 99.8, "standby", id="below-boundary-is-standby"),
            pytest.param({}, None, "standby", id="no-completion"),
            pytest.param({"printing": True}, 100.0, "printing", id="finishing"),
        ],
    )
    @pytest.mark.asyncio
    async def test_derives_the_state_from_flags_rather_than_percentage(
        self, flags: dict, completion: float | None, expected: str
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/printer":
                return httpx.Response(200, json={"state": {"flags": flags}})
            return httpx.Response(
                200,
                json={
                    "job": {"file": {"name": "cube.gcode"}},
                    "progress": {"completion": completion},
                },
            )

        client = _client(handler)
        snapshot = await client.query_snapshot()

        # `finishing` is the case the flags exist for: 100% *with* `printing`
        # still set is a print about to end, not one that ended. Reading the
        # percentage alone closes the job record while the nozzle is moving.
        assert snapshot.state == expected

    @pytest.mark.asyncio
    async def test_reports_standby_for_a_printer_with_no_file(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/printer":
                return httpx.Response(200, json={"state": {"flags": {}}})
            return httpx.Response(200, json={"job": {}, "progress": {}})

        client = _client(handler)

        assert (await client.query_snapshot()).state == "standby"

    @pytest.mark.asyncio
    async def test_survives_a_response_whose_fields_are_the_wrong_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/printer":
                return httpx.Response(200, json={"state": "not-an-object"})
            return httpx.Response(200, json={"job": [], "progress": "nope"})

        client = _client(handler)

        # A provider that answers with the wrong shape must not take the poll
        # loop down; the snapshot degrades instead.
        assert (await client.query_snapshot()).state == "standby"
