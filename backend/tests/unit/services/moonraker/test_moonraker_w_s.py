"""Defends moonraker w s at the services moonraker unit boundary.

A regression would mis-handle Moonraker transport, URL, or status semantics.
"""

from __future__ import annotations

from ._moonraker_shared import (
    SUBSCRIPTIONS,
    MoonrakerClient,
    MoonrakerError,
    asyncio,
    json,
    patch,
    pytest,
)


class TestMoonrakerWS:
    def _make_ws_messages(self, *payloads):
        """Yield raw WS message generators that feed to a mock WS."""
        for payload in payloads:
            yield json.dumps(payload)
        # Return an infinite wait (the actual subscribe loop reads forever)
        while True:
            yield asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_subscribe_receives_status_push(self, monkeypatch):
        client = MoonrakerClient("http://printer.local:7125")

        # Build status payload matching subscription response format
        status_data = {
            "print_stats": {"state": "printing", "filename": "test.gcode"},
            "virtual_sdcard": {"progress": 0.42},
            "heater_bed": {"temperature": 60.0, "target": 60.0},
            "extruder": {"temperature": 210.0, "target": 210.0},
            "toolhead": {"position": [100, 100, 50], "homed_axes": "xyz"},
            "webhooks": {"state": "ready", "state_message": "OK"},
        }

        # First message: subscribe response (with id=1 and result)
        subscribe_response = {
            "jsonrpc": "2.0",
            "method": "printer.objects.subscribe",
            "id": 1,
            "result": {"status": status_data},
        }

        # Second message: a push notification
        push_notification = {
            "jsonrpc": "2.0",
            "method": "notify_status_update",
            "params": [
                {
                    "print_stats": {"state": "paused"},
                    "virtual_sdcard": {"progress": 0.50},
                }
            ],
        }

        received: list = []

        async def on_status(status):
            received.append(status)

        # Create a controlled stop event
        stop = asyncio.Event()

        # Feed messages in sequence, then signal stop
        messages = iter([subscribe_response, push_notification])

        class MockWS:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def send(self, msg):
                pass

            async def recv(self):
                try:
                    msg = next(messages)
                    if isinstance(msg, dict):
                        return json.dumps(msg)
                    return msg
                except StopIteration as exc:
                    stop.set()
                    raise asyncio.CancelledError from exc

            async def ping(self):
                pass

        with patch("websockets.connect", return_value=MockWS()):
            task = asyncio.create_task(client.subscribe(on_status, stop_event=stop))
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            stop.set()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        assert len(received) >= 1
        # First received status should have print_stats
        first = received[0]
        assert "print_stats" in first
        assert first["print_stats"]["state"] == "printing"

    @pytest.mark.asyncio
    async def test_subscribe_identifies_with_api_key_before_subscribing(self):
        client = MoonrakerClient("http://printer.local:7125", api_key="secret123")
        sent: list[dict] = []
        stop = asyncio.Event()

        class MockWS:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def send(self, raw):
                sent.append(json.loads(raw))

            async def recv(self):
                request = sent[-1]
                if request["method"] == "server.connection.identify":
                    return json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "result": {"connection_id": 1},
                        }
                    )
                stop.set()
                return json.dumps(
                    {"jsonrpc": "2.0", "id": request["id"], "result": {"status": {}}}
                )

        with patch("websockets.connect", return_value=MockWS()):
            await client.subscribe(lambda _status: asyncio.sleep(0), stop_event=stop)

        assert [message["method"] for message in sent[:2]] == [
            "server.connection.identify",
            "printer.objects.subscribe",
        ]
        assert sent[0]["params"]["api_key"] == "secret123"

    @pytest.mark.asyncio
    async def test_subscribe_stops_on_event(self):
        client = MoonrakerClient("http://printer.local:7125")
        stop = asyncio.Event()
        stop.set()  # Pre-set so subscribe returns immediately

        async def on_status(status):
            pass

        await client.subscribe(on_status, stop_event=stop)
        # Should return without error

    def test_ws_url_http(self):
        client = MoonrakerClient("http://printer.local:7125")
        assert client._ws_url() == "ws://printer.local:7125/websocket"

    def test_ws_url_https(self):
        client = MoonrakerClient("https://printer.local:7125")
        assert client._ws_url() == "wss://printer.local:7125/websocket"

    def test_ws_url_strips_trailing_slash(self):
        client = MoonrakerClient("http://printer.local:7125/")
        assert client._ws_url() == "ws://printer.local:7125/websocket"

    @pytest.mark.asyncio
    async def test_subscribe_raises_on_identify_failure(self):
        client = MoonrakerClient("http://printer.local:7125", api_key="badkey")

        class MockWS:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def send(self, raw):
                self.last_request = json.loads(raw)

            async def recv(self):
                return json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": self.last_request["id"],
                        "error": {"message": "invalid api key"},
                    }
                )

        with patch("websockets.connect", return_value=MockWS()):
            with pytest.raises(MoonrakerError, match="authentication failed"):
                await client.subscribe(lambda _status: asyncio.sleep(0))

    @pytest.mark.asyncio
    async def test_subscribe_skips_malformed_json_message(self):
        client = MoonrakerClient("http://printer.local:7125")
        stop = asyncio.Event()
        received: list = []

        async def on_status(status):
            received.append(status)

        messages = iter(
            [
                "not valid json {{{",
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "notify_status_update",
                        "params": [{"print_stats": {"state": "printing"}}],
                    }
                ),
            ]
        )

        class MockWS:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def send(self, raw):
                pass

            async def recv(self):
                try:
                    return next(messages)
                except StopIteration:
                    stop.set()
                    return await asyncio.sleep(10)

        with patch("websockets.connect", return_value=MockWS()):
            task = asyncio.create_task(client.subscribe(on_status, stop_event=stop))
            await asyncio.wait_for(stop.wait(), timeout=2.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert received == [{"print_stats": {"state": "printing"}}]

    @pytest.mark.asyncio
    async def test_subscribe_pings_on_recv_timeout(self):
        client = MoonrakerClient("http://printer.local:7125")
        stop = asyncio.Event()
        pinged = asyncio.Event()

        class MockWS:
            def __init__(self):
                self.calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def send(self, raw):
                pass

            async def recv(self):
                self.calls += 1
                if self.calls == 1:
                    raise asyncio.TimeoutError
                stop.set()
                return await asyncio.sleep(10)

            async def ping(self):
                pinged.set()

        with patch("websockets.connect", return_value=MockWS()):
            task = asyncio.create_task(
                client.subscribe(lambda _s: asyncio.sleep(0), stop_event=stop)
            )
            await asyncio.wait_for(pinged.wait(), timeout=2.0)
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_subscribe_backs_off_and_reconnects_after_os_error(self):
        client = MoonrakerClient("http://printer.local:7125")
        stop = asyncio.Event()

        with patch(
            "websockets.connect", side_effect=OSError("network unreachable")
        ) as mock_connect:
            task = asyncio.create_task(
                client.subscribe(lambda _s: asyncio.sleep(0), stop_event=stop)
            )
            # Let the first connect attempt fail and enter the backoff wait.
            await asyncio.sleep(0.05)
            stop.set()
            await asyncio.wait_for(task, timeout=2.0)

        assert mock_connect.call_count >= 1


class TestSubscriptions:
    def test_subscriptions_has_required_objects(self):
        assert "print_stats" in SUBSCRIPTIONS
        assert "virtual_sdcard" in SUBSCRIPTIONS
        assert "heater_bed" in SUBSCRIPTIONS
        assert "extruder" in SUBSCRIPTIONS
        assert "toolhead" in SUBSCRIPTIONS
        assert "webhooks" in SUBSCRIPTIONS

    def test_print_stats_fields_include_state(self):
        assert "state" in SUBSCRIPTIONS["print_stats"]

    def test_virtual_sdcard_fields_include_progress(self):
        assert "progress" in SUBSCRIPTIONS["virtual_sdcard"]
