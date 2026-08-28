"""The Moonraker wire contract: URLs, headers, and the status stream.

Moonraker is the one provider whose live status arrives over a websocket rather
than by polling, and that subscription is the printer's only voice: if it stops
delivering, the fleet view keeps showing a print that finished an hour ago, and
nobody is told. So this file defends the parts of the loop that are easy to get
silently wrong — the identify handshake that a Moonraker with `api_key` set
*requires* before it will accept a subscription, the three different message
shapes the server uses to say "here is the status", and the reconnect, because a
websocket to a printer on a home network drops routinely and a subscription that
gives up after the first drop is indistinguishable from an offline printer.

The HTTP half is simpler but the URLs are the contract: every path here appears
verbatim in Moonraker's API, a filename goes through per-segment percent-encoding
(a model called `my part.gcode` must not become two path segments), and every
non-2xx becomes a `MoonrakerError` carrying the status so the failure reaches the
job record instead of a bare traceback.

`api_key` values here are obviously fake; a real one is a printer credential.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from printstash_core.printers.contracts import PrinterClient
from printstash_core.printers.models import (
    MoonrakerConfig,
    OctoPrintConfig,
    PrinterSnapshot,
)
from printstash_core.printers.moonraker import (
    SUBSCRIPTIONS,
    MoonrakerClient,
    MoonrakerError,
    MoonrakerFactory,
    _default_websocket_connector,
)
from printstash_core.printers.registry import ProviderError

API_KEY = "not-a-real-api-key"
BASE_URL = "http://printer.local:7125/"


def _client(handler: Any, *, api_key: str | None = API_KEY) -> MoonrakerClient:
    transport = httpx.MockTransport(handler)
    return MoonrakerClient(
        MoonrakerConfig(BASE_URL, api_key),
        http_client_factory=lambda: httpx.AsyncClient(transport=transport),
    )


class _FakeWebSocket:
    """A Moonraker websocket that replays a scripted list of server messages."""

    def __init__(self, messages: list[object]) -> None:
        self.sent: list[dict[str, Any]] = []
        self.pings = 0
        self._messages = list(messages)

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        if not self._messages:
            raise asyncio.TimeoutError
        message = self._messages.pop(0)
        if isinstance(message, BaseException):
            raise message
        if isinstance(message, str):
            return message
        return json.dumps(message)

    async def ping(self) -> None:
        self.pings += 1


def _connector(websocket: _FakeWebSocket):
    class Context:
        async def __aenter__(self) -> _FakeWebSocket:
            return websocket

        async def __aexit__(self, *_args: object) -> None:
            return None

    return lambda *_args, **_kwargs: Context()


def _identify_ok(request_id: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": {"connection_id": 7}}


def _subscribe_result(request_id: int, status: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": {"status": status}}


class TestDefaultWebsocketConnector:
    def test_builds_a_connection_without_opening_one(self) -> None:
        # `websockets.connect` is lazy: it only dials on `__aenter__`, which is
        # what makes it substitutable in every test below.
        connector = _default_websocket_connector("ws://printer.local:7125/websocket")

        assert hasattr(connector, "__aenter__")


class TestHeaders:
    @pytest.mark.asyncio
    async def test_sends_the_api_key_when_one_is_configured(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"result": {}})

        client = _client(handler)
        try:
            await client.info()
        finally:
            await client.aclose()

        assert seen[0].headers["X-Api-Key"] == API_KEY

    @pytest.mark.asyncio
    async def test_sends_no_key_header_when_none_is_configured(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"result": {}})

        client = _client(handler, api_key=None)
        try:
            await client.info()
        finally:
            await client.aclose()

        # An unauthenticated Moonraker rejects a request carrying an empty key.
        assert "X-Api-Key" not in seen[0].headers


class TestRequest:
    @pytest.mark.parametrize(
        ("call", "path"),
        [
            pytest.param("info", "/printer/info", id="printer-info"),
            pytest.param("server_info", "/server/info", id="server-info"),
            pytest.param("server_config", "/server/config", id="server-config"),
            pytest.param(
                "query_configfile", "/printer/objects/query", id="query-configfile"
            ),
            pytest.param(
                "printer_config", "/printer/objects/query", id="printer-config-alias"
            ),
            pytest.param(
                "list_gcode_files", "/server/files/list", id="list-gcode-files"
            ),
            pytest.param("emergency_stop", "/printer/emergency_stop", id="estop"),
        ],
    )
    @pytest.mark.asyncio
    async def test_calls_the_documented_path(self, call: str, path: str) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"result": {}})

        client = _client(handler)
        try:
            await getattr(client, call)()
        finally:
            await client.aclose()

        assert seen == [path]

    @pytest.mark.asyncio
    async def test_reports_a_transport_failure_as_a_provider_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = _client(handler)
        try:
            # The printer being unplugged must reach the job record as a provider
            # error, not as an httpx exception nobody upstream catches.
            with pytest.raises(MoonrakerError, match="transport error: no route"):
                await client.info()
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_carries_a_refusals_status_with_its_body(self) -> None:
        client = _client(lambda _request: httpx.Response(503, text="offline"))
        try:
            with pytest.raises(MoonrakerError, match="moonraker 503: offline"):
                await client.info()
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_keeps_a_non_json_success_body_as_raw_text(self) -> None:
        client = _client(lambda _request: httpx.Response(200, text="OK"))
        try:
            # Some Moonraker builds answer an action with a bare `ok`. That is a
            # success, so it must not be turned into an error.
            assert await client.info() == {"raw": "OK"}
        finally:
            await client.aclose()


class TestQueryStatus:
    @pytest.mark.asyncio
    async def test_asks_for_every_subscribed_object(self) -> None:
        seen: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            if request.url.path == "/server/spoolman/spool_id":
                return httpx.Response(200, json={"result": {"spool_id": 42}})
            return httpx.Response(200, json={"result": {"status": {}}})

        client = _client(handler)
        try:
            await client.query_status()
        finally:
            await client.aclose()

        assert set(seen[0].params) == set(SUBSCRIPTIONS)

    @pytest.mark.asyncio
    async def test_reports_the_active_spool_as_a_loaded_material_slot(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/server/spoolman/spool_id":
                return httpx.Response(200, json={"result": {"spool_id": 42}})
            return httpx.Response(
                200,
                json={"result": {"status": {"print_stats": {"state": "printing"}}}},
            )

        client = _client(handler)
        try:
            snapshot = await client.query_snapshot()
        finally:
            await client.aclose()

        assert (
            snapshot.material_slots[0].external_spool_id,
            snapshot.material_slots[0].state,
        ) == (42, "loaded")

    @pytest.mark.asyncio
    async def test_reports_an_unknown_slot_when_spoolman_is_not_configured(
        self,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/server/spoolman/spool_id":
                return httpx.Response(404, json={"error": "not configured"})
            return httpx.Response(
                200, json={"result": {"status": {"print_stats": {"state": "standby"}}}}
            )

        client = _client(handler)
        try:
            snapshot = await client.query_snapshot()
        finally:
            await client.aclose()

        # Spoolman is optional; not having it is not a printer fault.
        assert (
            snapshot.material_slots[0].state,
            snapshot.material_slots[0].external_spool_id,
        ) == ("unknown", None)

    @pytest.mark.asyncio
    async def test_accepts_a_bare_spool_id_instead_of_an_object(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/server/spoolman/spool_id":
                return httpx.Response(200, json={"result": 7})
            return httpx.Response(200, json={"result": {"status": {}}})

        client = _client(handler)
        try:
            status = await client.query_status()
        finally:
            await client.aclose()

        slots = status["result"]["status"]["material_slots"]
        assert slots[0]["external_spool_id"] == 7

    @pytest.mark.asyncio
    async def test_leaves_a_response_without_a_status_object_untouched(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/server/spoolman/spool_id":
                return httpx.Response(200, json={"result": {"spool_id": 1}})
            return httpx.Response(200, json={"result": "unexpected"})

        client = _client(handler)
        try:
            # Nowhere to attach the slots, and inventing a shape would hide the
            # fact that the printer answered with something unusable.
            assert await client.query_status() == {"result": "unexpected"}
        finally:
            await client.aclose()


class TestSpoolmanMaterialSlots:
    @pytest.mark.parametrize(
        "spool_id",
        [
            pytest.param(None, id="absent"),
            pytest.param(True, id="boolean"),
            pytest.param("not-a-number", id="unparseable-string"),
            pytest.param({"spool_id": 1}, id="object"),
        ],
    )
    def test_reports_unknown_for_anything_that_is_not_a_spool_id(
        self, spool_id: object
    ) -> None:
        slot = MoonrakerClient._spoolman_material_slots(spool_id)[0]

        # `True` is the one worth naming: in Python it is an int, so a
        # `spool_id: true` from a misbehaving Spoolman would become spool 1.
        assert (slot["state"], slot["external_spool_id"]) == ("unknown", None)

    @pytest.mark.parametrize(
        ("spool_id", "expected"),
        [
            pytest.param(42, 42, id="int"),
            pytest.param("42", 42, id="numeric-string"),
            pytest.param(42.0, 42, id="float"),
        ],
    )
    def test_normalizes_a_spool_id_to_an_int(
        self, spool_id: object, expected: int
    ) -> None:
        slot = MoonrakerClient._spoolman_material_slots(spool_id)[0]

        assert (slot["state"], slot["external_spool_id"]) == ("loaded", expected)


class TestListFiles:
    @pytest.mark.asyncio
    async def test_returns_the_reported_files(self) -> None:
        client = _client(
            lambda _request: httpx.Response(
                200, json={"result": [{"path": "cube.gcode"}]}
            )
        )
        try:
            assert await client.list_files() == [{"path": "cube.gcode"}]
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_returns_nothing_when_the_result_is_not_a_list(self) -> None:
        client = _client(lambda _request: httpx.Response(200, json={"result": None}))
        try:
            # A caller iterates this; a `None` here would raise three frames up.
            assert await client.list_files() == []
        finally:
            await client.aclose()


class TestDeleteFile:
    @pytest.mark.asyncio
    async def test_encodes_each_path_segment_separately(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.raw_path.decode())
            return httpx.Response(200, json={"result": "ok"})

        client = _client(handler)
        try:
            await client.delete_file("folder/my part.gcode")
        finally:
            await client.aclose()

        # Per-segment: the slash stays a separator and the space does not turn
        # the filename into two directories.
        assert seen == ["/server/files/gcodes/folder/my%20part.gcode"]


class TestUploadGcode:
    @pytest.mark.asyncio
    async def test_posts_the_file_to_the_gcodes_root(self, tmp_path: Path) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"result": "ok"})

        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        client = _client(handler)
        try:
            assert await client.upload(source, "cube.gcode") == {"result": "ok"}
        finally:
            await client.aclose()

        assert seen[0].url.path == "/server/files/upload"
        assert b'name="print"\r\n\r\nfalse' in seen[0].content

    @pytest.mark.asyncio
    async def test_asks_the_printer_to_start_when_told_to(self, tmp_path: Path) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"result": "ok"})

        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        client = _client(handler)
        try:
            await client.upload_gcode(source, "cube.gcode", start_print=True)
        finally:
            await client.aclose()

        assert b'name="print"\r\n\r\ntrue' in seen[0].content

    @pytest.mark.asyncio
    async def test_reports_a_transport_failure_during_upload(
        self, tmp_path: Path
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.WriteError("connection reset")

        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        client = _client(handler)
        try:
            # A reset mid-upload is the common failure on a busy printer, and it
            # must be distinguishable from a rejection.
            with pytest.raises(MoonrakerError, match="upload transport error"):
                await client.upload(source, "cube.gcode")
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_reports_a_rejected_upload_with_its_status(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        client = _client(lambda _request: httpx.Response(507, text="disk full"))
        try:
            with pytest.raises(MoonrakerError, match="upload failed 507: disk full"):
                await client.upload(source, "cube.gcode")
        finally:
            await client.aclose()


class TestPrintActions:
    @pytest.mark.parametrize(
        ("call", "method", "path"),
        [
            pytest.param("start", "POST", "/printer/print/start", id="start"),
            pytest.param("pause", "POST", "/printer/print/pause", id="pause"),
            pytest.param("resume", "POST", "/printer/print/resume", id="resume"),
            pytest.param("cancel", "POST", "/printer/print/cancel", id="cancel"),
        ],
    )
    @pytest.mark.asyncio
    async def test_uses_the_documented_endpoint(
        self, call: str, method: str, path: str
    ) -> None:
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            return httpx.Response(200, json={"result": "ok"})

        client = _client(handler)
        target = getattr(client, call)
        try:
            await (target("folder/cube.gcode") if call == "start" else target())
        finally:
            await client.aclose()

        assert seen == [(method, path)]

    @pytest.mark.asyncio
    async def test_names_the_file_to_print_as_a_query_parameter(self) -> None:
        seen: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json={"result": "ok"})

        client = _client(handler)
        try:
            await client.start_print("folder/cube.gcode")
        finally:
            await client.aclose()

        assert seen[0].params["filename"] == "folder/cube.gcode"

    @pytest.mark.asyncio
    async def test_sends_a_gcode_script_verbatim(self) -> None:
        seen: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json={"result": "ok"})

        client = _client(handler)
        try:
            await client.run_gcode("SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0")
        finally:
            await client.aclose()

        assert (
            seen[0].params["script"]
            == "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0"
        )


class TestGetPrintHistory:
    @pytest.mark.asyncio
    async def test_returns_the_reported_jobs(self) -> None:
        client = _client(
            lambda _request: httpx.Response(
                200, json={"result": {"jobs": [{"filename": "cube.gcode"}]}}
            )
        )
        try:
            assert await client.get_print_history() == [{"filename": "cube.gcode"}]
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_passes_the_requested_limit(self) -> None:
        seen: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json={"result": {"jobs": []}})

        client = _client(handler)
        try:
            await client.get_print_history(limit=5)
        finally:
            await client.aclose()

        assert seen[0].params["limit"] == "5"


class TestWsUrl:
    @pytest.mark.parametrize(
        ("base_url", "expected"),
        [
            pytest.param(
                "http://printer.local:7125",
                "ws://printer.local:7125/websocket",
                id="http-becomes-ws",
            ),
            pytest.param(
                "https://printer.local",
                "wss://printer.local/websocket",
                id="https-becomes-wss",
            ),
            pytest.param(
                "ws://printer.local", "ws://printer.local/websocket", id="already-ws"
            ),
        ],
    )
    def test_derives_the_websocket_url_from_the_base_url(
        self, base_url: str, expected: str
    ) -> None:
        client = MoonrakerClient(MoonrakerConfig(base_url, None))

        assert client._ws_url() == expected


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribes_to_every_object_it_polls(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket([_subscribe_result(1, {"print_stats": {}})])
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )

        async def on_status(_status: dict[str, Any]) -> None:
            stop.set()

        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        assert websocket.sent[0]["method"] == "printer.objects.subscribe"
        assert websocket.sent[0]["params"] == {"objects": SUBSCRIPTIONS}

    @pytest.mark.asyncio
    async def test_identifies_itself_before_subscribing_when_a_key_is_set(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket(
            [_identify_ok(), _subscribe_result(2, {"print_stats": {}})]
        )
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, API_KEY),
            websocket_connector=_connector(websocket),
        )

        async def on_status(_status: dict[str, Any]) -> None:
            stop.set()

        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        # A Moonraker with `api_key` set refuses a subscription from an
        # unidentified connection, and the key travels in the identify frame.
        assert websocket.sent[0]["method"] == "server.connection.identify"
        assert websocket.sent[0]["params"]["api_key"] == API_KEY
        assert websocket.sent[1]["method"] == "printer.objects.subscribe"

    @pytest.mark.asyncio
    async def test_refuses_to_subscribe_when_identify_reports_an_error(self) -> None:
        websocket = _FakeWebSocket(
            [{"jsonrpc": "2.0", "id": 1, "error": {"message": "invalid api key"}}]
        )
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, API_KEY),
            websocket_connector=_connector(websocket),
        )

        async def on_status(_status: dict[str, Any]) -> None:
            raise AssertionError("must not deliver status after a failed identify")

        # A wrong key is a configuration problem, not a transient drop: it is
        # raised rather than retried forever with a growing backoff.
        with pytest.raises(MoonrakerError, match="authentication failed: invalid"):
            await client.subscribe(on_status)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_refuses_to_subscribe_when_identify_answers_another_request(
        self,
    ) -> None:
        websocket = _FakeWebSocket([{"jsonrpc": "2.0", "id": 99, "result": {}}])
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, API_KEY),
            websocket_connector=_connector(websocket),
        )

        async def on_status(_status: dict[str, Any]) -> None:
            raise AssertionError("must not deliver status after a failed identify")

        # An id that does not match means the frames are out of step; trusting
        # the next one would subscribe on an unidentified connection.
        with pytest.raises(MoonrakerError, match="authentication failed"):
            await client.subscribe(on_status)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_delivers_a_pushed_status_update(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket(
            [
                {
                    "jsonrpc": "2.0",
                    "method": "notify_status_update",
                    "params": [{"print_stats": {"state": "printing"}}, 1.0],
                }
            ]
        )
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )
        seen: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            seen.append(status)
            stop.set()

        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        # This is the steady-state message: everything after the first snapshot
        # arrives this way.
        assert seen == [{"print_stats": {"state": "printing"}}]

    @pytest.mark.asyncio
    async def test_reports_a_spool_change_as_a_material_slot_update(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket(
            [{"jsonrpc": "2.0", "method": "notify_active_spool_set", "params": [42]}]
        )
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )
        seen: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            seen.append(status)
            stop.set()

        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        # The same slot shape the polling path produces, so a subscriber never
        # has to know which path delivered it.
        assert seen[0]["material_slots"][0]["external_spool_id"] == 42

    @pytest.mark.asyncio
    async def test_ignores_a_status_update_with_no_payload(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket(
            [
                {"jsonrpc": "2.0", "method": "notify_status_update", "params": []},
                {
                    "jsonrpc": "2.0",
                    "method": "notify_status_update",
                    "params": [{"print_stats": {}}],
                },
            ]
        )
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )
        seen: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            seen.append(status)
            stop.set()

        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        assert seen == [{"print_stats": {}}]

    @pytest.mark.asyncio
    async def test_ignores_a_frame_that_is_not_json(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket(
            ["<html>proxy error</html>", _subscribe_result(1, {"print_stats": {}})]
        )
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )
        seen: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            seen.append(status)
            stop.set()

        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        # A reverse proxy in front of the printer answers with HTML when it is
        # unhappy; one bad frame must not end the subscription.
        assert seen == [{"print_stats": {}}]

    @pytest.mark.asyncio
    async def test_ignores_a_subscribe_result_with_an_empty_status(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket(
            [
                _subscribe_result(1, {}),
                {
                    "jsonrpc": "2.0",
                    "method": "notify_status_update",
                    "params": [{"print_stats": {}}],
                },
            ]
        )
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )
        seen: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            seen.append(status)
            stop.set()

        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        assert seen == [{"print_stats": {}}]

    @pytest.mark.asyncio
    async def test_pings_an_idle_connection_instead_of_dropping_it(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket([])

        async def on_status(_status: dict[str, Any]) -> None:
            raise AssertionError("no status was delivered")

        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )
        task = asyncio.get_running_loop().create_task(
            client.subscribe(on_status, stop_event=stop)
        )
        await asyncio.sleep(0)
        stop.set()
        await task
        await client.aclose()

        # A printer sitting idle sends nothing for minutes; a receive timeout is
        # normal and is answered with a ping, not a reconnect.
        assert websocket.pings >= 1

    @pytest.mark.asyncio
    async def test_yields_between_pings_on_an_idle_connection(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket([])
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )

        async def on_status(_status: dict[str, Any]) -> None:
            raise AssertionError("no status was delivered")

        task = asyncio.get_running_loop().create_task(
            client.subscribe(on_status, stop_event=stop)
        )
        # Several scheduler hops with the loop idle. If the receive/ping loop
        # does not yield, nothing else on this event loop ever runs again — on
        # Python 3.12+, `asyncio.wait_for` awaits the coroutine directly rather
        # than wrapping it in a task, so a `recv()` that finishes without
        # suspending stopped providing the hop this loop used to rely on. The
        # symptom was the whole suite hanging on 3.13; in production it would be
        # the API process spinning at 100% and ignoring shutdown.
        for _ in range(20):
            await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, timeout=5.0)
        await client.aclose()

        assert websocket.pings >= 2

    @pytest.mark.asyncio
    async def test_returns_immediately_when_asked_to_stop_before_connecting(
        self,
    ) -> None:
        stop = asyncio.Event()
        stop.set()
        websocket = _FakeWebSocket([_subscribe_result(1, {"print_stats": {}})])
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )

        async def on_status(_status: dict[str, Any]) -> None:
            raise AssertionError("must not connect after being asked to stop")

        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        assert websocket.sent == []

    @pytest.mark.asyncio
    async def test_stops_backing_off_as_soon_as_it_is_asked_to_stop(self) -> None:
        stop = asyncio.Event()
        attempts: list[int] = []

        def failing_connector(*_args: object, **_kwargs: object):
            attempts.append(1)
            stop.set()
            raise OSError("connection refused")

        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None),
            websocket_connector=failing_connector,
            logger=logging.getLogger("test.moonraker"),
        )

        async def on_status(_status: dict[str, Any]) -> None:
            raise AssertionError("no connection was ever made")

        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        # Shutdown must not have to wait out a 30-second backoff.
        assert attempts == [1]

    @pytest.mark.asyncio
    async def test_reconnects_after_a_dropped_connection(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket([_subscribe_result(1, {"print_stats": {}})])
        attempts: list[int] = []

        def flaky_connector(*args: object, **kwargs: object):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("connection reset by peer")
            return _connector(websocket)(*args, **kwargs)

        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=flaky_connector
        )
        seen: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            seen.append(status)
            stop.set()

        # Real time, not a fake clock: the one-second first backoff *is* the
        # behaviour. A websocket to a printer on a home network drops routinely,
        # and a subscription that gives up after the first drop is
        # indistinguishable from an offline printer.
        await client.subscribe(on_status, stop_event=stop)
        await client.aclose()

        assert (len(attempts), seen) == (2, [{"print_stats": {}}])


class TestSubscribeSnapshots:
    @pytest.mark.asyncio
    async def test_delivers_the_same_payload_as_the_legacy_callback(self) -> None:
        status = {
            "print_stats": {"state": "paused", "filename": "cube.gcode"},
            "virtual_sdcard": {"progress": 0.5},
        }
        stop = asyncio.Event()
        websocket = _FakeWebSocket([_subscribe_result(1, status)])
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )
        received: list[PrinterSnapshot] = []

        async def on_snapshot(snapshot: PrinterSnapshot) -> None:
            received.append(snapshot)
            stop.set()

        await client.subscribe_snapshots(on_snapshot, stop_event=stop)
        await client.aclose()

        assert received[0].to_legacy_payload() == status

    @pytest.mark.asyncio
    async def test_subscribe_status_is_the_legacy_alias(self) -> None:
        stop = asyncio.Event()
        websocket = _FakeWebSocket([_subscribe_result(1, {"print_stats": {}})])
        client = MoonrakerClient(
            MoonrakerConfig(BASE_URL, None), websocket_connector=_connector(websocket)
        )
        seen: list[dict[str, Any]] = []

        async def on_status(status: dict[str, Any]) -> None:
            seen.append(status)
            stop.set()

        await client.subscribe_status(on_status, stop_event=stop)
        await client.aclose()

        assert seen == [{"print_stats": {}}]


class TestMoonrakerFactory:
    def test_builds_a_client_that_satisfies_the_provider_protocol(self) -> None:
        client = MoonrakerFactory().build(MoonrakerConfig("http://printer.local", None))

        assert isinstance(client, PrinterClient)

    def test_refuses_another_provider_s_configuration(self) -> None:
        # Every factory takes the same `PrinterConfig`, so this is the only thing
        # stopping an OctoPrint row from producing a Moonraker client that speaks
        # the wrong protocol at the printer.
        with pytest.raises(ProviderError, match="provider_config_mismatch"):
            MoonrakerFactory().build(OctoPrintConfig("http://printer.local", API_KEY))
