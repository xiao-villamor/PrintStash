"""Defends moonraker client h t t p at the services moonraker unit boundary.

A regression would mis-handle Moonraker transport, URL, or status semantics.
"""

from __future__ import annotations

from ._moonraker_shared import (
    AsyncMock,
    MagicMock,
    MoonrakerClient,
    MoonrakerError,
    Path,
    asyncio,
    httpx,
    patch,
    pytest,
)


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
