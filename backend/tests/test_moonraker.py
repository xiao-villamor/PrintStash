"""Tests for MoonrakerClient HTTP + WebSocket wrapper."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.moonraker import SUBSCRIPTIONS, MoonrakerClient, MoonrakerError


class TestMoonrakerClientHTTP:
    def test_info_returns_printer_info(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"hostname": "mainsail"}}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.info())
            assert result["result"]["hostname"] == "mainsail"

    def test_info_handles_http_error(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            with pytest.raises(MoonrakerError, match="moonraker 500"):
                asyncio.run(client.info())

    def test_info_rejects_redirect_status(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.text = "Found"

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            with pytest.raises(MoonrakerError, match="moonraker 302"):
                asyncio.run(client.info())

    def test_query_status_builds_correct_params(self):
        client = MoonrakerClient("http://printer.local:7125")
        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"result": {"status": {}}}
        spool_resp = MagicMock()
        spool_resp.status_code = 200
        spool_resp.json.return_value = {"result": {"spool_id": None}}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(
                side_effect=[status_resp, spool_resp]
            )
            result = asyncio.run(client.query_status())
            calls = mock_get_client.return_value.request.call_args_list
            call_args = calls[0]
            url = call_args[0][1]
            assert "/printer/objects/query?" in url
            assert "print_stats=" in url
            assert calls[1][0][1].endswith("/server/spoolman/spool_id")
            assert result == {
                "result": {
                    "status": {
                        "material_slots": [
                            {
                                "slot_key": "tool0",
                                "label": "Moonraker active spool",
                                "tool_key": "tool0",
                                "state": "unknown",
                                "external_spool_id": None,
                            }
                        ]
                    }
                }
            }

    def test_list_gcode_files_uses_gcodes_root(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": [{"path": "part.gcode", "size": 100}]}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.list_gcode_files())
            url = mock_get_client.return_value.request.call_args[0][1]
            assert url.endswith("/server/files/list?root=gcodes")
            assert result["result"][0]["path"] == "part.gcode"

    def test_upload_gcode(self, tmp_path: Path):
        gcode_path = tmp_path / "test.gcode"
        gcode_path.write_bytes(b"G1 X0 Y0 Z0\nG28\n")

        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "ok"}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.post = AsyncMock(return_value=mock_resp)
            result = asyncio.run(
                client.upload_gcode(gcode_path, "test.gcode", start_print=True)
            )
            assert result == {"result": "ok"}

    def test_upload_gcode_handles_error(self, tmp_path: Path):
        gcode_path = tmp_path / "test.gcode"
        gcode_path.write_bytes(b"G1 X0 Y0 Z0\n")

        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.post = AsyncMock(return_value=mock_resp)
            with pytest.raises(MoonrakerError, match="upload failed 400"):
                asyncio.run(client.upload_gcode(gcode_path, "test.gcode"))

    def test_start_print(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "ok"}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.start_print("my_print.gcode"))
            assert result == {"result": "ok"}
            call_args = mock_get_client.return_value.request.call_args
            assert call_args[0][1].endswith("/printer/print/start")
            assert call_args[1]["params"] == {"filename": "my_print.gcode"}

    def test_pause_resume_cancel(self):
        client = MoonrakerClient("http://printer.local:7125")

        for method in ("pause_print", "resume_print", "cancel_print"):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"result": "ok"}

            with patch("app.services.moonraker.get_http_client") as mock_get_client:
                mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
                result = asyncio.run(getattr(client, method)())
                assert result == {"result": "ok"}

    def test_api_key_sent_in_headers(self):
        client = MoonrakerClient("http://printer.local:7125", api_key="secret123")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {}}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            asyncio.run(client.info())
            call_kwargs = mock_get_client.return_value.request.call_args
            headers = call_kwargs[1].get("headers", {})
            assert headers.get("X-Api-Key") == "secret123"

    def test_request_transport_error_wraps_httpx_error(self):
        client = MoonrakerClient("http://printer.local:7125")

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(
                side_effect=httpx.ConnectError("connection refused")
            )
            with pytest.raises(MoonrakerError, match="transport error"):
                asyncio.run(client.info())

    def test_request_non_json_response_returns_raw(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = "plain text body"

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.info())
            assert result == {"raw": "plain text body"}

    def test_server_info(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"klippy_state": "ready"}}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.server_info())
            url = mock_get_client.return_value.request.call_args[0][1]
            assert url.endswith("/server/info")
            assert result["result"]["klippy_state"] == "ready"

    def test_server_config(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"server": {}}}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.server_config())
            url = mock_get_client.return_value.request.call_args[0][1]
            assert url.endswith("/server/config")
            assert result["result"]["server"] == {}

    def test_query_configfile(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"status": {"configfile": {}}}}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.query_configfile())
            url = mock_get_client.return_value.request.call_args[0][1]
            assert "configfile" in url
            assert result["result"]["status"] == {"configfile": {}}

    def test_delete_gcode_file_encodes_nested_path(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "ok"}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.delete_gcode_file("folder/my part.gcode"))
            assert result == {"result": "ok"}
            call_args = mock_get_client.return_value.request.call_args
            assert call_args[0][0] == "DELETE"
            assert call_args[0][1].endswith(
                "/server/files/gcodes/folder/my%20part.gcode"
            )

    def test_upload_gcode_transport_error(self, tmp_path: Path):
        gcode_path = tmp_path / "test.gcode"
        gcode_path.write_bytes(b"G28\n")
        client = MoonrakerClient("http://printer.local:7125")

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.post = AsyncMock(
                side_effect=httpx.ConnectError("refused")
            )
            with pytest.raises(MoonrakerError, match="upload transport error"):
                asyncio.run(client.upload_gcode(gcode_path, "test.gcode"))

    def test_run_gcode_builds_params(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "ok"}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.run_gcode("G28"))
            assert result == {"result": "ok"}
            call_args = mock_get_client.return_value.request.call_args
            assert call_args[0][1].endswith("/printer/gcode/script")
            assert call_args[1]["params"] == {"script": "G28"}

    def test_emergency_stop(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "ok"}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.emergency_stop())
            assert result == {"result": "ok"}
            call_args = mock_get_client.return_value.request.call_args
            assert call_args[0][1].endswith("/printer/emergency_stop")

    def test_get_print_history_returns_jobs_list(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"jobs": [{"filename": "a.gcode"}]}}

        with patch("app.services.moonraker.get_http_client") as mock_get_client:
            mock_get_client.return_value.request = AsyncMock(return_value=mock_resp)
            result = asyncio.run(client.get_print_history(limit=5))
            assert result == [{"filename": "a.gcode"}]
            call_args = mock_get_client.return_value.request.call_args
            assert call_args[1]["params"] == {"limit": 5}

    def test_ws_url_falls_back_for_unknown_scheme(self):
        client = MoonrakerClient("printer.local:7125")
        assert client._ws_url() == "printer.local:7125/websocket"


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
