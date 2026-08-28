"""The MQTT session: TLS setup, printer identity, and one request/response.

Bambu LAN mode is the only provider whose transport security PrintStash has to
assemble itself. The printer presents a certificate signed by Bambu's own CA
whose common name is the *serial number*, not the LAN address it answers on, so
ordinary hostname verification cannot pass. The client therefore pins Bambu's CA
bundle, disables paho's hostname check, and re-does the identity check itself
against the serial. Every one of those three steps is load-bearing: drop the CA
bundle and nothing connects; drop the identity check and `tls_insecure_set(True)`
turns into an open door where any device on the LAN can impersonate the printer
and receive its access code.

The request path matters for a duller reason. Bambu answers on a topic rather
than a connection, so a command is only correlated to its reply by sequence id,
and a publish that lands before the subscription is silently lost. If this file
goes red on call ordering, commands will appear to succeed and never execute.
"""

from __future__ import annotations

import hashlib
import ssl
from typing import Any

import pytest

import printstash_core.printers.bambu as bambu_module
from printstash_core.printers.bambu import (
    _BAMBU_CA_CERTIFICATES,
    BambuClient,
    _ImplicitFTP_TLS,
)
from printstash_core.printers.models import ProviderError

from .conftest import (
    ACCESS_CODE,
    HOST,
    REPORT_TOPIC,
    REQUEST_TOPIC,
    SERIAL,
    FakeMqttClient,
    ScriptedMqttClient,
    make_client,
)

# A response to a `pause` command with the sequence id the test sends.
PAUSE_ACCEPTED = {"print": {"command": "pause", "result": "success"}}
# Short enough that a timeout assertion costs milliseconds, not seconds.
BRIEF = 0.05


class FakeSSLContext:
    def __init__(self) -> None:
        self.check_hostname = True
        self.cadata: str | None = None

    def load_verify_locations(self, *, cadata: str) -> None:
        self.cadata = cadata


def peer(common_name: str) -> Any:
    """A client whose TLS peer presents `common_name` in its subject."""

    class PeerSocket:
        def getpeercert(self) -> dict[str, object]:
            return {"subject": ((("commonName", common_name),),)}

    class Client:
        def socket(self) -> PeerSocket:
            return PeerSocket()

    return Client()


class TestBambuCaCertificates:
    def test_pins_exactly_the_three_certificate_bambu_chain(self) -> None:
        assert _BAMBU_CA_CERTIFICATES.count("-----BEGIN CERTIFICATE-----") == 3

    def test_pins_the_bundle_byte_for_byte(self) -> None:
        digest = hashlib.sha256(_BAMBU_CA_CERTIFICATES.encode()).hexdigest()

        # A characterization hash, not a value to update when it fails. The
        # bundle is a trust root: if it changed, someone must say why.
        assert digest == (
            "6b9c885ddb23796b1487f8a7bbdeb044a20404b3f1c8bdc0b9a1706f57bd4511"
        )


class TestMqttClient:
    def test_returns_the_injected_client(self) -> None:
        mqtt = FakeMqttClient()

        assert make_client(mqtt_client_factory=lambda: mqtt)._mqtt_client() is mqtt

    def test_authenticates_as_the_lan_mode_user_with_the_access_code(self) -> None:
        mqtt = FakeMqttClient()

        make_client(mqtt_client_factory=lambda: mqtt)._mqtt_client()

        # `bblp` is fixed by Bambu's LAN mode; the access code is the secret.
        assert mqtt.credentials == ("bblp", ACCESS_CODE)

    def test_loads_the_pinned_bambu_ca_bundle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        context = FakeSSLContext()
        monkeypatch.setattr(bambu_module.ssl, "create_default_context", lambda: context)
        mqtt = FakeMqttClient()

        make_client(mqtt_client_factory=lambda: mqtt)._mqtt_client()

        assert context.cadata == _BAMBU_CA_CERTIFICATES
        assert mqtt.context is context

    def test_disables_hostname_verification(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        context = FakeSSLContext()
        monkeypatch.setattr(bambu_module.ssl, "create_default_context", lambda: context)
        mqtt = FakeMqttClient()

        make_client(mqtt_client_factory=lambda: mqtt)._mqtt_client()

        # Paho would otherwise compare the LAN IP against the serial-number CN
        # and fail every connection. `_validate_mqtt_peer` does that comparison
        # correctly after the handshake instead.
        assert context.check_hostname is False
        assert mqtt.insecure is True

    def test_reports_a_missing_mqtt_dependency_as_a_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_paho(name: str) -> Any:
            raise ModuleNotFoundError(name)

        monkeypatch.setattr(bambu_module, "import_module", no_paho)

        with pytest.raises(ProviderError) as error:
            make_client()._mqtt_client()

        # A self-hoster on a slim image gets a named cause, not an ImportError
        # traceback out of a background thread.
        assert error.value.code == "provider_dependency_missing"
        assert error.value.detail == "bambu_mqtt_dependency_missing"


class TestValidateMqttPeer:
    def test_accepts_a_certificate_naming_the_configured_serial(self) -> None:
        make_client()._validate_mqtt_peer(peer(SERIAL))

    def test_skips_the_check_when_the_client_exposes_no_socket(self) -> None:
        make_client()._validate_mqtt_peer(object())

    def test_rejects_a_certificate_naming_another_printer(self) -> None:
        with pytest.raises(ProviderError) as error:
            make_client()._validate_mqtt_peer(peer("OTHER-SERIAL"))

        # The whole point of the manual check: without it, any LAN device with a
        # Bambu-signed certificate could collect this printer's access code.
        assert error.value.code == "provider_authentication_failed"

    def test_rejects_a_client_whose_tls_socket_is_gone(self) -> None:
        class Disconnected:
            def socket(self) -> None:
                return None

        with pytest.raises(ProviderError) as error:
            make_client()._validate_mqtt_peer(Disconnected())

        assert error.value.code == "provider_transport_error"

    def test_rejects_a_peer_presenting_no_certificate_subject(self) -> None:
        class Anonymous:
            def socket(self) -> Any:
                return type("S", (), {"getpeercert": lambda self: None})()

        with pytest.raises(ProviderError) as error:
            make_client()._validate_mqtt_peer(Anonymous())

        assert error.value.code == "provider_authentication_failed"


class TestConnectionFailed:
    def test_treats_reason_code_zero_as_success(self) -> None:
        assert BambuClient._connection_failed(0) is False

    def test_treats_a_non_zero_reason_code_as_failure(self) -> None:
        assert BambuClient._connection_failed(5) is True

    def test_prefers_the_paho_v2_is_failure_flag_over_the_numeric_code(self) -> None:
        # Paho's v2 callback passes a ReasonCode object whose truthiness says
        # nothing useful; only `is_failure` does.
        succeeded = type("ReasonCode", (), {"is_failure": False})()

        assert BambuClient._connection_failed(succeeded) is False


class TestMqttRequest:
    def test_subscribes_before_it_publishes_anything(self) -> None:
        wire = ScriptedMqttClient(messages=[PAUSE_ACCEPTED])
        client = make_client(mqtt_client_factory=lambda: wire)
        payload = {"print": {"command": "pause"}}

        response = client._mqtt_request(
            payload, accepts=lambda body: body["print"]["command"] == "pause"
        )

        assert response == PAUSE_ACCEPTED
        # The order is the contract: Bambu drops a publish that arrives before
        # the subscription, and leaves the socket open if `loop_stop` is missed.
        assert wire.calls == [
            ("connect", HOST, 8883, 30),
            ("subscribe", REPORT_TOPIC, 1),
            ("loop_start",),
            ("publish", REQUEST_TOPIC, payload, 1, False),
            ("disconnect",),
            ("loop_stop",),
        ]

    def test_ignores_a_report_the_caller_did_not_ask_for(self) -> None:
        wire = ScriptedMqttClient(
            messages=[{"print": {"command": "resume", "result": "success"}}]
        )
        client = make_client(mqtt_client_factory=lambda: wire)

        with pytest.raises(ProviderError) as error:
            client._mqtt_request(
                {"print": {"command": "pause"}},
                accepts=lambda body: body["print"]["command"] == "pause",
                timeout=BRIEF,
            )

        # Bambu answers on a shared topic, so an unrelated reply must not be
        # mistaken for this command's — that would report success wrongly.
        assert error.value.detail == "bambu_response_timeout"

    def test_ignores_a_report_that_is_not_valid_json(self) -> None:
        wire = ScriptedMqttClient(messages=[b"\xff not json", PAUSE_ACCEPTED])
        client = make_client(mqtt_client_factory=lambda: wire)

        response = client._mqtt_request(
            {"print": {"command": "pause"}},
            accepts=lambda body: body["print"]["command"] == "pause",
        )

        # A garbled frame must not kill the session; the next one still counts.
        assert response == PAUSE_ACCEPTED

    def test_times_out_when_the_printer_never_acknowledges_the_connection(self) -> None:
        wire = ScriptedMqttClient(fire_connect=False)
        client = make_client(mqtt_client_factory=lambda: wire)

        with pytest.raises(ProviderError) as error:
            client._mqtt_request(
                {"print": {"command": "pause"}},
                accepts=lambda _body: True,
                timeout=BRIEF,
            )

        assert error.value.code == "provider_timeout"
        assert error.value.detail == "bambu_mqtt_connect_timeout"

    def test_times_out_when_the_printer_never_answers(self) -> None:
        wire = ScriptedMqttClient(deliver=False)
        client = make_client(mqtt_client_factory=lambda: wire)

        with pytest.raises(ProviderError) as error:
            client._mqtt_request(
                {"print": {"command": "pause"}},
                accepts=lambda _body: True,
                timeout=BRIEF,
            )

        assert error.value.code == "provider_timeout"
        assert error.value.detail == "bambu_response_timeout"

    def test_surfaces_a_refused_connection_as_an_authentication_failure(self) -> None:
        wire = ScriptedMqttClient(reason_code=5)
        client = make_client(mqtt_client_factory=lambda: wire)

        with pytest.raises(ProviderError) as error:
            client._mqtt_request(
                {"print": {"command": "pause"}}, accepts=lambda _body: True
            )

        # Reason code 5 is "not authorised", which for LAN mode means the
        # access code is wrong — the one cause a self-hoster can act on.
        assert error.value.code == "provider_authentication_failed"
        assert "mqtt connection refused: 5" in error.value.detail

    def test_surfaces_a_printer_identity_mismatch_before_publishing(self) -> None:
        wire = ScriptedMqttClient(peer_common_name="OTHER-SERIAL")
        client = make_client(mqtt_client_factory=lambda: wire)

        with pytest.raises(ProviderError) as error:
            client._mqtt_request(
                {"print": {"command": "pause"}}, accepts=lambda _body: True
            )

        assert error.value.code == "provider_authentication_failed"
        assert [call[0] for call in wire.calls] == [
            "connect",
            "loop_start",
            "disconnect",
            "loop_stop",
        ]

    def test_refuses_to_wait_for_a_command_the_broker_would_not_accept(self) -> None:
        wire = ScriptedMqttClient(publish_rc=4, deliver=False)
        client = make_client(mqtt_client_factory=lambda: wire)

        with pytest.raises(ProviderError) as error:
            client._mqtt_request(
                {"print": {"command": "pause"}},
                accepts=lambda _body: True,
                timeout=BRIEF,
            )

        # Failing fast on the publish return code beats waiting out the reply
        # timeout for a message that was never sent.
        assert error.value.code == "provider_transport_error"
        assert error.value.detail == "bambu_command_not_published"


class TestSendCommand:
    @pytest.mark.asyncio
    async def test_accepts_only_the_reply_to_its_own_request(self) -> None:
        client = make_client()
        seen: dict[str, Any] = {}

        def request(payload: dict[str, Any], *, accepts: Any, **_: Any) -> Any:
            request_body = payload["print"]
            reply = {
                "print": {
                    "sequence_id": request_body["sequence_id"],
                    "command": request_body["command"],
                    "result": "success",
                }
            }
            seen["accepted"] = accepts(reply)
            return reply

        client._mqtt_request = request  # type: ignore[method-assign]

        assert await client._send_command(
            {"print": {"sequence_id": "42", "command": "pause"}}
        ) == {"ok": True}
        assert seen["accepted"] is True

    @pytest.mark.asyncio
    async def test_rejects_a_reply_carrying_another_sequence_id(self) -> None:
        client = make_client()
        verdicts: list[bool] = []

        def request(payload: dict[str, Any], *, accepts: Any, **_: Any) -> Any:
            other = {"print": {"command": "pause", "result": "success"}}
            verdicts.append(accepts(other))
            return {
                "print": {
                    "sequence_id": "42",
                    "command": "pause",
                    "result": "success",
                }
            }

        client._mqtt_request = request  # type: ignore[method-assign]

        await client._send_command({"print": {"sequence_id": "42", "command": "pause"}})

        # Two commands in flight would otherwise collect each other's replies.
        assert verdicts == [False]

    @pytest.mark.asyncio
    async def test_surfaces_the_printers_own_reason_for_refusing(self) -> None:
        client = make_client()
        client._mqtt_request = lambda *_a, **_k: {  # type: ignore[method-assign]
            "print": {"result": "failed", "reason": "busy"}
        }

        with pytest.raises(ProviderError) as error:
            await client._send_command(
                {"print": {"sequence_id": "42", "command": "pause"}}
            )

        assert error.value.code == "provider_command_rejected"
        assert error.value.detail == "bambu command rejected by printer: busy"

    @pytest.mark.asyncio
    async def test_names_an_unexplained_refusal(self) -> None:
        client = make_client()
        client._mqtt_request = lambda *_a, **_k: {  # type: ignore[method-assign]
            "print": {"result": "failed"}
        }

        with pytest.raises(ProviderError) as error:
            await client._send_command(
                {"print": {"sequence_id": "42", "command": "pause"}}
            )

        assert error.value.detail == "bambu command rejected by printer: unknown reason"

    @pytest.mark.asyncio
    async def test_wraps_an_unexpected_transport_failure(self) -> None:
        client = make_client()

        def explode(*_a: Any, **_k: Any) -> None:
            raise OSError("host unreachable")

        client._mqtt_request = explode  # type: ignore[method-assign]

        with pytest.raises(ProviderError) as error:
            await client._send_command(
                {"print": {"sequence_id": "42", "command": "pause"}}
            )

        # Callers above this seam handle ProviderError only; a bare OSError
        # escaping here becomes a 500 rather than a printer-offline message.
        assert error.value.code == "provider_transport_error"
        assert error.value.detail == "host unreachable"


class TestFtpsClient:
    def test_returns_the_injected_client(self) -> None:
        ftp = object()

        assert make_client(ftps_client_factory=lambda: ftp)._ftps_client() is ftp

    def test_builds_an_implicit_tls_client_for_bambus_port_990(self) -> None:
        ftp = make_client()._ftps_client()

        # `ftplib` speaks explicit TLS (AUTH TLS after a plaintext greeting);
        # Bambu's port 990 wraps the socket before the greeting.
        assert isinstance(ftp, _ImplicitFTP_TLS)
        assert ftp.timeout == 30

    def test_accepts_the_printers_device_local_self_signed_certificate(self) -> None:
        ftp = make_client()._ftps_client()

        # There is no CA that can vouch for a device certificate on a LAN
        # address, and the access code is what actually authenticates here.
        assert ftp.context.check_hostname is False
        assert ftp.context.verify_mode == ssl.CERT_NONE
