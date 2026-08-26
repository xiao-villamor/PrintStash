"""Defends bambu transport at the services printer provider unit boundary.

A regression would misclassify provider capability, status, or transport behavior.
"""

from __future__ import annotations

from ._printer_provider_shared import (
    AsyncMock,
    BambuLanProvider,
    MagicMock,
    PacketTypes,
    Path,
    ProviderError,
    ReasonCode,
    _fake_mqtt_client,
    asyncio,
    json,
    mqtt,
    patch,
    pytest,
)


class TestBambuLanProvider:
    def test_mqtt_client_uses_bambu_ca_and_manual_serial_validation(self):
        client = MagicMock()
        provider = BambuLanProvider(
            "192.168.1.50", "SN123", "acc", mqtt_client_factory=lambda: client
        )

        assert provider._mqtt_client() is client
        client.tls_set_context.assert_called_once()
        client.tls_insecure_set.assert_called_once_with(True)

    def test_bambu_mqtt_rejects_certificate_for_another_printer(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = MagicMock()
        client.socket.return_value.getpeercert.return_value = {
            "subject": ((("commonName", "other-printer"),),)
        }

        with pytest.raises(ProviderError, match="certificate identity mismatch"):
            provider._validate_mqtt_peer(client)

    def test_normalize_status_maps_expected_shape(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        out = provider._normalize_status(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "mc_percent": 45,
                    "subtask_name": "cube.gcode",
                }
            }
        )
        # RUNNING is translated to Moonraker's "printing" vocabulary.
        assert out["print_stats"]["state"] == "printing"
        assert out["print_stats"]["filename"] == "cube.gcode"
        assert out["virtual_sdcard"]["progress"] == pytest.approx(0.45)

    @pytest.mark.parametrize(
        "bambu_state, moonraker_state",
        [
            ("RUNNING", "printing"),
            ("PAUSE", "paused"),  # regression: was passed through as "pause"
            ("FINISH", "complete"),  # regression: was "finish" -> UNKNOWN downstream
            ("FAILED", "error"),
            ("IDLE", "standby"),
            ("PREPARE", "standby"),
            ("SLICING", "standby"),
        ],
    )
    def test_normalize_status_translates_bambu_vocabulary(
        self, bambu_state, moonraker_state
    ):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        out = provider._normalize_status({"print": {"gcode_state": bambu_state}})
        assert out["print_stats"]["state"] == moonraker_state

    def test_normalize_status_progress_is_clamped(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        # Out-of-range mc_percent must not escape the 0..1 progress band.
        assert (
            provider._normalize_status({"print": {"mc_percent": 150}})[
                "virtual_sdcard"
            ]["progress"]
            == 1.0
        )

    def test_normalize_status_does_not_erase_fields_missing_from_partial_report(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")

        assert provider._normalize_status({"print": {"mc_percent": 50}}) == {
            "virtual_sdcard": {"progress": 0.5}
        }
        assert provider._normalize_status({"print": {"wifi_signal": "-45dBm"}}) == {}
        assert provider._normalize_status({"print": {"mc_percent": None}}) == {}

    def test_normalized_bambu_states_are_known_to_status_map(self):
        # Every translated state must resolve to a concrete (non-UNKNOWN)
        # PrinterStatus, proving the provider and hub vocabularies agree.
        from app.db.models import PrinterStatus
        from app.services.printer_hub import _derive_printer_status

        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        for bambu_state in ("RUNNING", "PAUSE", "FINISH", "FAILED", "IDLE"):
            snap = provider._normalize_status({"print": {"gcode_state": bambu_state}})
            _, vault_status = _derive_printer_status(snap)
            assert vault_status != PrinterStatus.UNKNOWN, bambu_state

    def test_pause_resume_cancel_commands(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with patch.object(provider, "_send_command", new_callable=AsyncMock) as send:
            send.return_value = {"ok": True}
            asyncio.run(provider.pause())
            asyncio.run(provider.resume())
            asyncio.run(provider.cancel())
            assert send.await_count == 3

    def test_start_sends_cached_gcode_command(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with patch.object(provider, "_send_command", new_callable=AsyncMock) as send:
            send.return_value = {"ok": True}
            assert asyncio.run(provider.start("file.gcode")) == {"ok": True}
        payload = send.await_args.args[0]
        assert payload["print"]["command"] == "gcode_file"
        assert payload["print"]["param"] == "/cache/file.gcode"
        assert payload["print"]["sequence_id"]

    def test_upload_uses_ftps_adapter(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        with patch.object(provider, "_upload_via_ftps") as upload:
            assert asyncio.run(provider.upload(source, "cube.gcode")) == {
                "ok": True,
                "remote_filename": "cube.gcode",
            }
        upload.assert_called_once_with(source, "cube.gcode")

    def test_upload_rejects_nested_remote_name(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        with pytest.raises(ProviderError, match="invalid_bambu_remote_filename"):
            provider._upload_via_ftps(source, "nested/cube.gcode")

    def test_ftps_upload_uses_cache_and_atomic_rename(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        ftp = MagicMock()
        ftp.size.return_value = source.stat().st_size
        with patch.object(provider, "_ftps_client", return_value=ftp):
            provider._upload_via_ftps(source, "cube.gcode")

        ftp.connect.assert_called_once_with("192.168.1.50", 990)
        ftp.login.assert_called_once_with("bblp", "acc")
        ftp.prot_p.assert_called_once_with()
        upload_path = ftp.storbinary.call_args.args[0]
        assert upload_path.startswith("STOR cache/.cube.gcode.")
        assert upload_path.endswith(".uploading")
        temp_path = upload_path.removeprefix("STOR ")
        ftp.size.assert_called_once_with(temp_path)
        ftp.rename.assert_called_once_with(temp_path, "cache/cube.gcode")
        ftp.quit.assert_called_once_with()

    def test_ftps_upload_falls_back_to_close_when_quit_fails(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        ftp = MagicMock()
        ftp.size.return_value = source.stat().st_size
        ftp.quit.side_effect = OSError("connection reset")
        with patch.object(provider, "_ftps_client", return_value=ftp):
            provider._upload_via_ftps(source, "cube.gcode")
        ftp.close.assert_called_once_with()

    def test_ftps_upload_size_mismatch_raises(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        ftp = MagicMock()
        ftp.size.return_value = source.stat().st_size + 1
        with patch.object(provider, "_ftps_client", return_value=ftp):
            with pytest.raises(ProviderError, match="bambu_upload_size_mismatch"):
                provider._upload_via_ftps(source, "cube.gcode")

    def test_ftps_download_retrieves_only_bounded_cache_artifacts(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        destination = tmp_path / "cube.gcode"
        ftp = MagicMock()
        ftp.size.return_value = 4

        def retrieve(command, callback):
            assert command == "RETR cache/cube.gcode"
            callback(b"G28\n")

        ftp.retrbinary.side_effect = retrieve
        with patch.object(provider, "_ftps_client", return_value=ftp):
            provider._download_via_ftps(
                "ftps://192.168.1.50/cache/cube.gcode",
                destination,
                max_bytes=1024,
            )

        assert destination.read_bytes() == b"G28\n"

    @pytest.mark.parametrize(
        "remote_path",
        [
            "https://example.com/cache/cube.gcode",
            "ftps://evil.example/cache/cube.gcode",
            "/cache/../secrets.gcode",
            "/config/printer.cfg",
        ],
    )
    def test_ftps_download_rejects_untrusted_paths(
        self, tmp_path: Path, remote_path: str
    ):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with pytest.raises(ProviderError):
            provider._download_via_ftps(
                remote_path, tmp_path / "capture.gcode", max_bytes=1024
            )

    def test_upload_wraps_ftps_provider_error(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        with patch.object(
            provider,
            "_upload_via_ftps",
            side_effect=ProviderError("bad", code="provider_error"),
        ):
            with pytest.raises(ProviderError, match="bad"):
                asyncio.run(provider.upload(source, "cube.gcode"))

    def test_upload_wraps_unexpected_exception(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        with patch.object(
            provider, "_upload_via_ftps", side_effect=RuntimeError("disk full")
        ):
            with pytest.raises(ProviderError) as exc:
                asyncio.run(provider.upload(source, "cube.gcode"))
        assert exc.value.code == "provider_transport_error"

    def test_validate_mqtt_peer_raises_when_socket_unavailable(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = MagicMock()
        client.socket.return_value = None
        with pytest.raises(ProviderError, match="TLS socket unavailable"):
            provider._validate_mqtt_peer(client)

    def test_mqtt_request_connection_refused_raises_auth_error(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = _fake_mqtt_client()

        def fake_connect(host, port, keepalive=30):
            client.on_connect(client, None, {}, 5, None)

        client.connect.side_effect = fake_connect
        with patch.object(provider, "_mqtt_client", return_value=client):
            with pytest.raises(ProviderError) as exc:
                provider._mqtt_request(
                    {"print": {}}, accepts=lambda _b: True, timeout=0.2
                )
        assert exc.value.code == "provider_authentication_failed"

    def test_mqtt_request_connect_timeout(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = _fake_mqtt_client()
        # connect() never invokes on_connect, so `connected` is never set.
        with patch.object(provider, "_mqtt_client", return_value=client):
            with pytest.raises(ProviderError) as exc:
                provider._mqtt_request(
                    {"print": {}}, accepts=lambda _b: True, timeout=0.05
                )
        assert exc.value.code == "provider_timeout"
        assert "connect_timeout" in exc.value.detail

    def test_mqtt_request_not_published_raises(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = _fake_mqtt_client()

        def fake_connect(host, port, keepalive=30):
            client.on_connect(client, None, {}, 0, None)

        client.connect.side_effect = fake_connect
        publish_info = MagicMock()
        publish_info.rc = mqtt.MQTT_ERR_NO_CONN
        client.publish.return_value = publish_info
        with patch.object(provider, "_mqtt_client", return_value=client):
            with pytest.raises(ProviderError) as exc:
                provider._mqtt_request(
                    {"print": {}}, accepts=lambda _b: True, timeout=0.2
                )
        assert "not_published" in exc.value.detail

    def test_mqtt_request_accepts_paho_v2_reason_code_and_response_without_puback(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = _fake_mqtt_client()
        publish_info = MagicMock()
        publish_info.rc = mqtt.MQTT_ERR_SUCCESS
        publish_info.is_published.return_value = False

        def fake_connect(host, port, keepalive=30):
            reason = ReasonCode(PacketTypes.CONNACK, identifier=0)
            client.on_connect(client, None, {}, reason, None)

        def fake_publish(topic, payload, qos=1, retain=False):
            message = MagicMock()
            message.payload = json.dumps({"print": {"gcode_state": "IDLE"}}).encode()
            client.on_message(client, None, message)
            return publish_info

        client.connect.side_effect = fake_connect
        client.publish.side_effect = fake_publish
        with patch.object(provider, "_mqtt_client", return_value=client):
            result = provider._mqtt_request(
                {"pushing": {}},
                accepts=lambda body: body.get("print", {}).get("gcode_state") == "IDLE",
                timeout=0.2,
            )

        assert result["print"]["gcode_state"] == "IDLE"
        publish_info.wait_for_publish.assert_not_called()

    def test_mqtt_request_disconnects_before_stopping_network_loop(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = _fake_mqtt_client()
        cleanup: list[str] = []

        def fake_connect(host, port, keepalive=30):
            client.on_connect(client, None, {}, 0, None)

        def fake_publish(topic, payload, qos=1, retain=False):
            message = MagicMock()
            message.payload = json.dumps({"print": {"ok": True}}).encode()
            client.on_message(client, None, message)
            info = MagicMock()
            info.rc = mqtt.MQTT_ERR_SUCCESS
            return info

        client.connect.side_effect = fake_connect
        client.publish.side_effect = fake_publish
        client.disconnect.side_effect = lambda: cleanup.append("disconnect")
        client.loop_stop.side_effect = lambda: cleanup.append("loop_stop")
        with patch.object(provider, "_mqtt_client", return_value=client):
            provider._mqtt_request(
                {"print": {}}, accepts=lambda body: "print" in body, timeout=0.2
            )

        assert cleanup == ["disconnect", "loop_stop"]

    def test_mqtt_request_response_timeout(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = _fake_mqtt_client()

        def fake_connect(host, port, keepalive=30):
            client.on_connect(client, None, {}, 0, None)

        client.connect.side_effect = fake_connect
        publish_info = MagicMock()
        publish_info.is_published.return_value = True
        client.publish.return_value = publish_info
        # on_message never fires, so `received` never fires either.
        with patch.object(provider, "_mqtt_client", return_value=client):
            with pytest.raises(ProviderError) as exc:
                provider._mqtt_request(
                    {"print": {}}, accepts=lambda _b: True, timeout=0.05
                )
        assert "response_timeout" in exc.value.detail

    def test_mqtt_request_ignores_malformed_message_payload(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = _fake_mqtt_client()

        def fake_connect(host, port, keepalive=30):
            client.on_connect(client, None, {}, 0, None)

        def fake_publish(topic, payload, qos=1, retain=False):
            # Malformed message first (should be silently ignored), then a
            # well-formed one that satisfies `accepts`.
            bad = MagicMock()
            bad.payload = b"\xff\xfe not json"
            client.on_message(client, None, bad)
            good = MagicMock()
            good.payload = json.dumps({"print": {"ok": True}}).encode()
            client.on_message(client, None, good)
            info = MagicMock()
            info.is_published.return_value = True
            return info

        client.connect.side_effect = fake_connect
        client.publish.side_effect = fake_publish
        with patch.object(provider, "_mqtt_client", return_value=client):
            result = provider._mqtt_request(
                {"print": {}}, accepts=lambda body: "print" in body, timeout=1.0
            )
        assert result == {"print": {"ok": True}}

    def test_send_command_wraps_unexpected_exception(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with patch.object(provider, "_mqtt_request", side_effect=RuntimeError("boom")):
            with pytest.raises(ProviderError) as exc:
                asyncio.run(provider._send_command({"print": {"command": "pause"}}))
        assert exc.value.code == "provider_transport_error"

    def test_query_status_wraps_unexpected_exception(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with patch.object(provider, "_mqtt_request", side_effect=RuntimeError("boom")):
            with pytest.raises(ProviderError) as exc:
                asyncio.run(provider.query_status())
        assert exc.value.code == "provider_transport_error"

    def test_query_status_passes_through_provider_error_unwrapped(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with patch.object(
            provider,
            "_mqtt_request",
            side_effect=ProviderError(
                "bambu_response_timeout", code="provider_timeout"
            ),
        ):
            with pytest.raises(ProviderError) as exc:
                asyncio.run(provider.query_status())
        assert exc.value.code == "provider_timeout"

    def test_ftps_client_builds_implicit_tls_ftp(self):
        client = BambuLanProvider._ftps_client()
        assert client.__class__.__name__ == "_ImplicitFTP_TLS"

    def test_ftps_upload_swallows_close_failure_after_quit_failure(
        self, tmp_path: Path
    ):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        ftp = MagicMock()
        ftp.size.return_value = source.stat().st_size
        ftp.quit.side_effect = OSError("connection reset")
        ftp.close.side_effect = OSError("already closed")
        with patch.object(provider, "_ftps_client", return_value=ftp):
            # Both cleanup calls fail; the upload itself must not raise.
            provider._upload_via_ftps(source, "cube.gcode")

    def test_start_rejects_nested_remote_filename(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with pytest.raises(ProviderError, match="invalid_bambu_remote_filename"):
            asyncio.run(provider.start("sub/dir/cube.gcode"))

    def test_subscribe_status_returns_immediately_when_stop_already_set(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        stop = asyncio.Event()
        stop.set()

        async def on_status(_status):
            pass

        # Should return without ever building an MQTT client.
        with patch.object(provider, "_mqtt_client") as mqtt_client:
            asyncio.run(provider.subscribe_status(on_status, stop_event=stop))
        mqtt_client.assert_not_called()

    def test_subscribe_status_raises_on_connection_refused(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = _fake_mqtt_client()

        def fake_connect(host, port, keepalive=30):
            client.on_connect(client, None, {}, 5, None)

        client.connect.side_effect = fake_connect

        async def on_status(_status):
            pass

        with patch.object(provider, "_mqtt_client", return_value=client):
            with pytest.raises(ProviderError) as exc:
                asyncio.run(provider.subscribe_status(on_status))
        assert exc.value.code == "provider_authentication_failed"
