"""Tests for MoonrakerClient HTTP + WebSocket wrapper."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.moonraker import MoonrakerClient, MoonrakerError, SUBSCRIPTIONS


class TestMoonrakerClientHTTP:
    def test_info_returns_printer_info(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"hostname": "mainsail"}}

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_resp
            )
            result = asyncio.run(client.info())
            assert result["result"]["hostname"] == "mainsail"

    def test_info_handles_http_error(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_resp
            )
            with pytest.raises(MoonrakerError, match="moonraker 500"):
                asyncio.run(client.info())

    def test_info_rejects_redirect_status(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.text = "Found"

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_resp
            )
            with pytest.raises(MoonrakerError, match="moonraker 302"):
                asyncio.run(client.info())

    def test_query_status_builds_correct_params(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {"status": {}}}

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_resp
            )
            result = asyncio.run(client.query_status())
            call_args = (
                MockClient.return_value.__aenter__.return_value.request.call_args
            )
            url = call_args[0][1]
            assert "/printer/objects/query?" in url
            assert "print_stats=" in url
            assert result == {"result": {"status": {}}}

    def test_list_gcode_files_uses_gcodes_root(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": [{"path": "part.gcode", "size": 100}]}

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_resp
            )
            result = asyncio.run(client.list_gcode_files())
            url = MockClient.return_value.__aenter__.return_value.request.call_args[0][
                1
            ]
            assert url.endswith("/server/files/list?root=gcodes")
            assert result["result"][0]["path"] == "part.gcode"

    def test_upload_gcode(self, tmp_path: Path):
        gcode_path = tmp_path / "test.gcode"
        gcode_path.write_bytes(b"G1 X0 Y0 Z0\nG28\n")

        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "ok"}

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
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

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            with pytest.raises(MoonrakerError, match="upload failed 400"):
                asyncio.run(client.upload_gcode(gcode_path, "test.gcode"))

    def test_start_print(self):
        client = MoonrakerClient("http://printer.local:7125")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": "ok"}

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_resp
            )
            result = asyncio.run(client.start_print("my_print.gcode"))
            assert result == {"result": "ok"}

    def test_pause_resume_cancel(self):
        client = MoonrakerClient("http://printer.local:7125")

        for method in ("pause_print", "resume_print", "cancel_print"):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"result": "ok"}

            with patch("httpx.AsyncClient") as MockClient:
                MockClient.return_value.__aenter__.return_value.request = AsyncMock(
                    return_value=mock_resp
                )
                result = asyncio.run(getattr(client, method)())
                assert result == {"result": "ok"}

    def test_api_key_sent_in_headers(self):
        client = MoonrakerClient("http://printer.local:7125", api_key="secret123")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"result": {}}

        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.request = AsyncMock(
                return_value=mock_resp
            )
            asyncio.run(client.info())
            call_kwargs = (
                MockClient.return_value.__aenter__.return_value.request.call_args
            )
            headers = call_kwargs[1].get("headers", {})
            assert headers.get("X-Api-Key") == "secret123"


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
                except StopIteration:
                    stop.set()
                    raise asyncio.CancelledError

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
