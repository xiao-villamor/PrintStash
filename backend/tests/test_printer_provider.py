from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import Printer, PrinterProvider
from app.services.printer_provider import (
    BambuLanProvider,
    BaseProvider,
    Capability,
    ElegooCentauriProvider,
    MoonrakerProvider,
    OctoPrintProvider,
    ProviderCapabilities,
    ProviderError,
    PrusaLinkProvider,
    capabilities_for_provider,
    detect_printer_model,
    get_provider_client,
)


class TestCapabilities:
    def test_moonraker_capabilities(self):
        caps = capabilities_for_provider(PrinterProvider.MOONRAKER)
        assert caps.can_upload is True
        assert caps.can_pause is True
        assert caps.support_level == "stable"

    def test_bambu_capabilities(self):
        caps = capabilities_for_provider(PrinterProvider.BAMBU_LAN)
        assert caps.can_upload is True
        assert caps.can_start is True
        assert caps.can_pause is True
        assert caps.support_level == "beta"
        assert "list_files" in caps.unsupported_actions

    def test_prusalink_capabilities_are_beta_and_honest(self):
        caps = PrusaLinkProvider.capabilities
        assert caps.can_upload is True
        assert caps.can_start is True
        assert caps.can_list_files is True
        assert caps.can_send_gcode is False
        assert caps.can_measure_consumption is False
        assert caps.support_level == "beta"

    def test_centauri_capabilities_are_safe_and_honest(self):
        caps = ElegooCentauriProvider.capabilities
        assert caps.can_live_status is True
        assert caps.can_start is True
        assert caps.can_pause is True
        assert caps.can_upload is False
        assert caps.can_list_files is False
        assert caps.can_send_gcode is False
        assert caps.support_level == "beta"

    def test_octoprint_capabilities_are_beta_and_honest(self):
        caps = OctoPrintProvider.capabilities
        assert caps.can_upload is True
        assert caps.can_start is True
        assert caps.can_list_files is True
        assert caps.can_send_gcode is False
        assert caps.can_measure_consumption is False
        assert caps.support_level == "beta"


class TestDetectPrinterModel:
    def test_detects_bambu_model_from_serial_prefix(self):
        p = Printer(
            name="X1C",
            provider=PrinterProvider.BAMBU_LAN,
            bambu_serial="01P00A123456",
        )
        assert detect_printer_model(p) == "Bambu Lab X1 Carbon"

    def test_unknown_bambu_serial_prefix_returns_none(self):
        p = Printer(
            name="Mystery",
            provider=PrinterProvider.BAMBU_LAN,
            bambu_serial="ZZZ00A123456",
        )
        assert detect_printer_model(p) is None

    def test_detects_elegoo_neptune4_from_provider_variant(self):
        p = Printer(
            name="Neptune",
            provider=PrinterProvider.MOONRAKER,
            provider_variant="elegoo_neptune4",
        )
        assert detect_printer_model(p) == "Elegoo Neptune 4 family"

    def test_detects_elegoo_centauri_carbon_2_from_provider_variant(self):
        p = Printer(
            name="Centauri",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon_2",
        )
        assert detect_printer_model(p) == "Elegoo Centauri Carbon 2"

    def test_plain_moonraker_is_undetectable(self):
        p = Printer(name="Voron", provider=PrinterProvider.MOONRAKER)
        assert detect_printer_model(p) is None


class TestProviderFactory:
    def test_get_moonraker_provider(self):
        p = Printer(
            name="mk",
            provider=PrinterProvider.MOONRAKER,
            moonraker_url="http://10.0.0.1:7125",
        )
        client = get_provider_client(p)
        assert isinstance(client, MoonrakerProvider)

    def test_get_bambu_provider(self):
        p = Printer(
            name="bambu",
            provider=PrinterProvider.BAMBU_LAN,
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="acc",
        )
        client = get_provider_client(p)
        assert isinstance(client, BambuLanProvider)

    def test_get_prusalink_digest_provider(self):
        p = Printer(
            name="mk4",
            provider=PrinterProvider.PRUSALINK,
            prusalink_url="http://mk4.local",
            prusalink_auth_mode="digest",
            prusalink_username="maker",
            prusalink_password="secret",
        )
        client = get_provider_client(p)
        assert isinstance(client, PrusaLinkProvider)

    def test_prusalink_missing_credentials_rejected(self):
        p = Printer(
            name="mk4",
            provider=PrinterProvider.PRUSALINK,
            prusalink_url="http://mk4.local",
            prusalink_auth_mode="digest",
            prusalink_username="maker",
        )
        with pytest.raises(ProviderError) as exc:
            get_provider_client(p)
        assert exc.value.code == "provider_credentials_missing"

    def test_get_centauri_carbon_provider(self):
        p = Printer(
            name="CC1",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon",
            elegoo_centauri_host="192.168.1.50",
        )
        assert isinstance(get_provider_client(p), ElegooCentauriProvider)

    def test_centauri_carbon_2_requires_access_code(self):
        p = Printer(
            name="CC2",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon_2",
            elegoo_centauri_host="192.168.1.51",
        )
        with pytest.raises(ProviderError) as exc:
            get_provider_client(p)
        assert exc.value.code == "provider_credentials_missing"

    def test_missing_bambu_creds_raises(self):
        p = Printer(
            name="bambu",
            provider=PrinterProvider.BAMBU_LAN,
            moonraker_url="",
        )
        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            get_provider_client(p)

    def test_get_octoprint_provider(self):
        p = Printer(
            name="octopi",
            provider=PrinterProvider.OCTOPRINT,
            octoprint_url="http://octopi.local",
            octoprint_api_key="key-123",
        )
        client = get_provider_client(p)
        assert isinstance(client, OctoPrintProvider)

    def test_octoprint_missing_credentials_rejected(self):
        p = Printer(
            name="octopi",
            provider=PrinterProvider.OCTOPRINT,
            octoprint_url="http://octopi.local",
        )
        with pytest.raises(ProviderError) as exc:
            get_provider_client(p)
        assert exc.value.code == "provider_credentials_missing"


def _fake_mqtt_client() -> MagicMock:
    """MagicMock shaped like the real paho client but *without* a ``socket``
    attribute, so ``_validate_mqtt_peer``'s "real paho has it" bypass applies
    (mirrors tests/e2e/fakes/mock_bambu.FakeMqttClient, which is only wired
    for the full print-flow integration tests, not raw MQTT error branches)."""
    return MagicMock(
        spec=[
            "username_pw_set",
            "tls_set_context",
            "tls_insecure_set",
            "connect",
            "subscribe",
            "loop_start",
            "loop_stop",
            "disconnect",
            "publish",
            "on_connect",
            "on_message",
        ]
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
        assert (
            provider._normalize_status({"print": {"mc_percent": None}})[
                "virtual_sdcard"
            ]["progress"]
            == 0.0
        )

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
        publish_info.is_published.return_value = False
        client.publish.return_value = publish_info
        with patch.object(provider, "_mqtt_client", return_value=client):
            with pytest.raises(ProviderError) as exc:
                provider._mqtt_request(
                    {"print": {}}, accepts=lambda _b: True, timeout=0.2
                )
        assert "not_published" in exc.value.detail

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

    def test_subscribe_status_raises_when_peer_cert_mismatches(self):
        # Connection is accepted (reason_code=0) but the post-handshake
        # identity check in on_connect fails — a different failure path than
        # the connection-refused case above.
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = MagicMock()
        client.socket.return_value.getpeercert.return_value = {
            "subject": ((("commonName", "other-printer"),),)
        }

        def fake_connect(host, port, keepalive=30):
            client.on_connect(client, None, {}, 0, None)

        client.connect.side_effect = fake_connect

        async def on_status(_status):
            pass

        with patch.object(provider, "_mqtt_client", return_value=client):
            with pytest.raises(ProviderError) as exc:
                asyncio.run(provider.subscribe_status(on_status))
        assert exc.value.code == "provider_authentication_failed"
        assert "certificate identity mismatch" in exc.value.detail

    def test_subscribe_status_ignores_malformed_and_non_print_messages(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        client = _fake_mqtt_client()
        received: list = []

        async def on_status(status):
            received.append(status)

        def fake_connect(host, port, keepalive=30):
            client.on_connect(client, None, {}, 0, None)

        def fake_publish(topic, payload, qos=1, retain=False):
            bad = MagicMock()
            bad.payload = b"\xff\xfe not json"
            client.on_message(client, None, bad)
            no_print_key = MagicMock()
            no_print_key.payload = json.dumps({"other": True}).encode()
            client.on_message(client, None, no_print_key)
            good = MagicMock()
            good.payload = json.dumps({"print": {"gcode_state": "IDLE"}}).encode()
            client.on_message(client, None, good)

        client.connect.side_effect = fake_connect
        client.publish.side_effect = fake_publish
        with patch.object(provider, "_mqtt_client", return_value=client):
            asyncio.run(
                asyncio.wait_for(provider.subscribe_status(on_status), timeout=3.0)
            )
        assert len(received) == 1
        assert received[0]["print_stats"]["state"] == "standby"


class TestMoonrakerProviderListFiles:
    def test_list_files_returns_result_list(self):
        provider = MoonrakerProvider("http://10.0.0.1:7125")
        with patch.object(
            provider.client,
            "list_gcode_files",
            new_callable=AsyncMock,
            return_value={"result": [{"path": "a.gcode"}]},
        ):
            files = asyncio.run(provider.list_files())
        assert files == [{"path": "a.gcode"}]

    def test_list_files_tolerates_non_list_result(self):
        provider = MoonrakerProvider("http://10.0.0.1:7125")
        with patch.object(
            provider.client,
            "list_gcode_files",
            new_callable=AsyncMock,
            return_value={"result": {"unexpected": "shape"}},
        ):
            files = asyncio.run(provider.list_files())
        assert files == []


class TestBaseProviderDefaults:
    """BaseProvider's per-method bodies are the fallback every registered
    provider currently overrides. Exercised directly here to document (and
    lock in) the abstract contract: capability-gated, then NotImplementedError."""

    class _FullySupportedProvider(BaseProvider):
        provider = PrinterProvider.MOONRAKER
        capabilities = ProviderCapabilities(supported=frozenset(Capability))

    def test_unimplemented_methods_raise_not_implemented(self):
        provider = self._FullySupportedProvider()
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.info())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.server_info())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.server_config())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.printer_config())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.query_status())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.list_files())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.upload(Path("x"), "x.gcode"))
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.delete_file("x.gcode"))
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.start("x.gcode"))
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.pause())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.resume())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.cancel())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.run_gcode("G28"))
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.emergency_stop())
        with pytest.raises(NotImplementedError):
            asyncio.run(provider.subscribe_status(AsyncMock()))
