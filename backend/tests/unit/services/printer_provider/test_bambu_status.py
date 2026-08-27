"""Defends bambu status at the services printer provider unit boundary.

A regression would misclassify provider capability, status, or transport behavior.
"""

from __future__ import annotations

from ._printer_provider_shared import (
    AsyncMock,
    BambuLanProvider,
    BaseProvider,
    Capability,
    MagicMock,
    MoonrakerProvider,
    Path,
    PrinterProvider,
    ProviderCapabilities,
    ProviderError,
    _fake_mqtt_client,
    asyncio,
    json,
    patch,
    pytest,
)


class TestBambuLanProvider:
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
