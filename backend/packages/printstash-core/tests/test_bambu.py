from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import printstash_core.printers.bambu as bambu_module
from printstash_core.printers.bambu import (
    _BAMBU_CA_CERTIFICATES,
    BambuClient,
    BambuFactory,
    _ImplicitFTP_TLS,
)
from printstash_core.printers.contracts import (
    ArtifactCaptureClient,
    PrinterClient,
)
from printstash_core.printers.models import (
    BambuConfig,
    OctoPrintConfig,
    ProviderError,
    ProviderId,
)
from printstash_core.printers.registry import ProviderRegistry


def make_client(**kwargs: Any) -> BambuClient:
    return BambuClient(BambuConfig("192.0.2.10", "TEST-SERIAL", "test-code"), **kwargs)


class FakeMqttClient:
    def __init__(self) -> None:
        self.credentials: tuple[str, str] | None = None
        self.context: Any = None
        self.insecure: bool | None = None

    def username_pw_set(self, username: str, password: str) -> None:
        self.credentials = (username, password)

    def tls_set_context(self, context: Any) -> None:
        self.context = context

    def tls_insecure_set(self, insecure: bool) -> None:
        self.insecure = insecure


class FakeFtpsClient:
    def __init__(
        self, *, remote_size: int | None = None, download: bytes = b""
    ) -> None:
        self.remote_size = remote_size
        self.download = download
        self.calls: list[tuple[Any, ...]] = []
        self.uploaded = b""

    def connect(self, host: str, port: int) -> None:
        self.calls.append(("connect", host, port))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", username, password))

    def prot_p(self) -> None:
        self.calls.append(("prot_p",))

    def storbinary(self, command: str, source: Any) -> None:
        self.calls.append(("storbinary", command))
        self.uploaded = source.read()

    def size(self, remote_name: str) -> int | None:
        self.calls.append(("size", remote_name))
        if self.remote_size is not None:
            return self.remote_size
        return len(self.uploaded or self.download)

    def rename(self, source: str, destination: str) -> None:
        self.calls.append(("rename", source, destination))

    def retrbinary(self, command: str, callback: Any) -> None:
        self.calls.append(("retrbinary", command))
        callback(self.download)

    def quit(self) -> None:
        self.calls.append(("quit",))

    def close(self) -> None:
        self.calls.append(("close",))


def test_bambu_ca_bundle_is_the_characterized_three_certificate_chain() -> None:
    assert _BAMBU_CA_CERTIFICATES.count("-----BEGIN CERTIFICATE-----") == 3
    assert hashlib.sha256(_BAMBU_CA_CERTIFICATES.encode()).hexdigest() == (
        "6b9c885ddb23796b1487f8a7bbdeb044a20404b3f1c8bdc0b9a1706f57bd4511"
    )


def test_normalize_status_exposes_ams_and_external_material_slots() -> None:
    status = make_client()._normalize_status(
        {
            "print": {
                "gcode_state": "idle",
                "nozzle_diameter": "0.4",
                "ams": {
                    "ams": [
                        {
                            "id": "0",
                            "tray": [
                                {
                                    "id": "0",
                                    "tray_type": "PLA",
                                    "tray_color": "FF0000FF",
                                },
                                {"id": "1", "tray_type": "", "tray_color": ""},
                            ],
                        }
                    ]
                },
                "vt_tray": {"tray_type": "PETG", "tray_color": "00FF00FF"},
            }
        }
    )

    assert status["material_slots"] == [
        {
            "slot_key": "ams:0:0",
            "label": "AMS 0 tray 0",
            "tool_key": "tool0",
            "state": "loaded",
            "material_type": "PLA",
            "color_hex": "#FF0000",
        },
        {
            "slot_key": "ams:0:1",
            "label": "AMS 0 tray 1",
            "tool_key": "tool0",
            "state": "empty",
            "material_type": None,
            "color_hex": None,
        },
        {
            "slot_key": "external",
            "label": "External spool",
            "tool_key": "tool0",
            "state": "loaded",
            "material_type": "PETG",
            "color_hex": "#00FF00",
        },
    ]
    assert status["material_tools"] == [
        {"tool_key": "tool0", "label": "Tool 0", "nozzle_diameter_mm": 0.4}
    ]


def test_ams_incremental_empty_and_malformed_updates_preserve_effective_snapshot() -> (
    None
):
    client = make_client()
    client._normalize_status(
        {
            "print": {
                "ams": {
                    "ams": [
                        {
                            "id": "0",
                            "tray": [
                                {
                                    "id": "0",
                                    "tray_type": "PLA",
                                    "tray_color": "FF0000FF",
                                },
                                {
                                    "id": "1",
                                    "tray_type": "PETG",
                                    "tray_color": "00FF00FF",
                                },
                            ],
                        }
                    ]
                }
            }
        }
    )
    incremental = client._normalize_status(
        {
            "print": {
                "ams": {
                    "ams": [
                        {
                            "id": "0",
                            "tray": [{"id": "1", "tray_type": "", "tray_color": ""}],
                        }
                    ]
                }
            }
        }
    )
    malformed = client._normalize_status({"print": {"ams": {"ams": [None, "bad"]}}})

    by_key = {row["slot_key"]: row for row in incremental["material_slots"]}
    assert by_key["ams:0:0"]["material_type"] == "PLA"
    assert by_key["ams:0:1"]["state"] == "empty"
    assert malformed["material_slots"] == incremental["material_slots"]


def test_mqtt_setup_loads_ca_and_restores_serial_identity_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        def __init__(self) -> None:
            self.check_hostname = True
            self.cadata: str | None = None

        def load_verify_locations(self, *, cadata: str) -> None:
            self.cadata = cadata

    context = FakeContext()
    mqtt = FakeMqttClient()
    monkeypatch.setattr(bambu_module.ssl, "create_default_context", lambda: context)
    client = make_client(mqtt_client_factory=lambda: mqtt)

    assert client._mqtt_client() is mqtt
    assert mqtt.credentials == ("bblp", "test-code")
    assert mqtt.context is context
    assert context.cadata == _BAMBU_CA_CERTIFICATES
    assert context.check_hostname is False
    assert mqtt.insecure is True


def test_mqtt_peer_must_match_configured_serial() -> None:
    class PeerSocket:
        def __init__(self, common_name: str) -> None:
            self.common_name = common_name

        def getpeercert(self) -> dict[str, object]:
            return {"subject": ((("commonName", self.common_name),),)}

    class Client:
        def __init__(self, common_name: str) -> None:
            self.peer = PeerSocket(common_name)

        def socket(self) -> PeerSocket:
            return self.peer

    client = make_client()
    client._validate_mqtt_peer(Client("TEST-SERIAL"))

    with pytest.raises(ProviderError) as error:
        client._validate_mqtt_peer(Client("OTHER-SERIAL"))
    assert error.value.code == "provider_authentication_failed"


def test_ftps_setup_preserves_implicit_tls_and_self_signed_certificate_policy() -> None:
    client = make_client()

    ftp = client._ftps_client()

    assert isinstance(ftp, _ImplicitFTP_TLS)
    assert ftp.timeout == 30
    assert ftp.context.check_hostname is False
    assert ftp.context.verify_mode == ssl.CERT_NONE


@pytest.mark.asyncio
async def test_upload_uses_atomic_cache_name_and_existing_result_shape(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cube.gcode"
    source.write_bytes(b"G28\n")
    ftp = FakeFtpsClient()
    client = make_client(
        ftps_client_factory=lambda: ftp,
        sequence_id_factory=lambda: "fixed-id",
    )

    result = await client.upload(source, "cube.gcode")

    temporary = "cache/.cube.gcode.fixed-id.uploading"
    assert result == {"ok": True, "remote_filename": "cube.gcode"}
    assert ftp.uploaded == b"G28\n"
    assert ftp.calls == [
        ("connect", "192.0.2.10", 990),
        ("login", "bblp", "test-code"),
        ("prot_p",),
        ("storbinary", f"STOR {temporary}"),
        ("size", temporary),
        ("rename", temporary, "cache/cube.gcode"),
        ("quit",),
    ]


@pytest.mark.asyncio
async def test_upload_rejects_paths_and_size_mismatches(tmp_path: Path) -> None:
    source = tmp_path / "cube.gcode"
    source.write_bytes(b"G28\n")
    client = make_client(ftps_client_factory=lambda: FakeFtpsClient(remote_size=99))

    with pytest.raises(ProviderError) as invalid:
        await client.upload(source, "folder/cube.gcode")
    assert invalid.value.code == "provider_error"
    assert invalid.value.detail == "invalid_bambu_remote_filename"

    with pytest.raises(ProviderError) as mismatch:
        await client.upload(source, "cube.gcode")
    assert mismatch.value.code == "provider_error"
    assert mismatch.value.detail == "bambu_upload_size_mismatch"


@pytest.mark.asyncio
async def test_artifact_download_keeps_path_and_byte_limit_guards(
    tmp_path: Path,
) -> None:
    ftp = FakeFtpsClient(download=b"1234")
    client = make_client(ftps_client_factory=lambda: ftp)
    destination = tmp_path / "benchy.3mf"

    await client.download_artifact(
        "ftps://192.0.2.10/cache/benchy.3mf", destination, max_bytes=4
    )

    assert destination.read_bytes() == b"1234"
    assert ("retrbinary", "RETR cache/benchy.3mf") in ftp.calls

    with pytest.raises(ProviderError) as invalid_host:
        await client.download_artifact(
            "ftps://other.invalid/cache/benchy.3mf",
            destination,
            max_bytes=4,
        )
    assert invalid_host.value.detail == "invalid_bambu_artifact_host"

    too_large = make_client(
        ftps_client_factory=lambda: FakeFtpsClient(download=b"12345")
    )
    with pytest.raises(ProviderError) as limit:
        await too_large.download_artifact("benchy.3mf", destination, max_bytes=4)
    assert limit.value.detail == "bambu_artifact_too_large"


@pytest.mark.asyncio
async def test_control_commands_preserve_wire_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[dict[str, Any]] = []

    async def send(payload: dict[str, Any]) -> dict[str, Any]:
        payloads.append(payload)
        return {"ok": True}

    client = make_client(sequence_id_factory=lambda: "sequence-42")
    monkeypatch.setattr(client, "_send_command", send)

    assert await client.start("cube.gcode") == {"ok": True}
    assert await client.pause() == {"ok": True}
    assert await client.resume() == {"ok": True}
    assert await client.cancel() == {"ok": True}
    assert payloads == [
        {
            "print": {
                "sequence_id": "sequence-42",
                "command": "gcode_file",
                "param": "/cache/cube.gcode",
            }
        },
        {"print": {"sequence_id": "0", "command": "pause"}},
        {"print": {"sequence_id": "0", "command": "resume"}},
        {"print": {"sequence_id": "0", "command": "stop"}},
    ]


@pytest.mark.asyncio
async def test_command_response_correlation_and_rejection_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()

    def success(
        payload: dict[str, Any], *, accepts: Any, timeout: float = 10.0
    ) -> dict[str, Any]:
        del timeout
        request = payload["print"]
        response = {
            "print": {
                "sequence_id": request["sequence_id"],
                "command": request["command"],
                "result": "success",
            }
        }
        assert accepts(response) is True
        return response

    monkeypatch.setattr(client, "_mqtt_request", success)
    assert await client._send_command(
        {"print": {"sequence_id": "42", "command": "pause"}}
    ) == {"ok": True}

    def rejected(
        payload: dict[str, Any], *, accepts: Any, timeout: float = 10.0
    ) -> dict[str, Any]:
        del payload, accepts, timeout
        return {"print": {"result": "failed", "reason": "busy"}}

    monkeypatch.setattr(client, "_mqtt_request", rejected)
    with pytest.raises(ProviderError) as error:
        await client._send_command({"print": {"sequence_id": "42", "command": "pause"}})
    assert error.value.code == "provider_command_rejected"
    assert error.value.detail == "bambu command rejected by printer: busy"


def test_status_normalization_preserves_state_progress_and_external_metadata() -> None:
    client = make_client()
    report = {
        "print": {
            "gcode_state": "RUNNING",
            "mc_percent": 42,
            "gcode_file": "/cache/plate_1.gcode",
            "print_error": "",
            "subtask_name": "Benchy",
            "task_id": "task-42",
            "subtask_id": "subtask-7",
            "project_id": "project-3",
            "profile_id": "profile-2",
            "plate_num": 1,
            "layer_num": 8,
            "total_layer_num": 120,
            "nozzle_diameter": 0.4,
        }
    }

    assert client._normalize_status(report) == {
        "print_stats": {
            "state": "printing",
            "filename": "plate_1.gcode",
            "message": "",
            "external_display_name": "Benchy",
            "external_task_id": "task-42",
            "external_subtask_id": "subtask-7",
            "external_project_id": "project-3",
            "external_profile_id": "profile-2",
            "external_gcode_file": "/cache/plate_1.gcode",
            "external_plate_index": 1,
            "external_current_layer": 8,
            "external_total_layers": 120,
            "external_nozzle_diameter": 0.4,
        },
        "virtual_sdcard": {"progress": 0.42},
        "material_tools": [
            {
                "tool_key": "tool0",
                "label": "Tool 0",
                "nozzle_diameter_mm": 0.4,
            }
        ],
    }


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("IDLE", "standby"),
        ("PREPARE", "standby"),
        ("SLICING", "standby"),
        ("RUNNING", "printing"),
        ("PAUSE", "paused"),
        ("FINISH", "complete"),
        ("FAILED", "error"),
        ("CUSTOM", "custom"),
    ],
)
def test_state_vocabulary_is_unchanged(raw: str, normalized: str) -> None:
    assert make_client()._normalize_status({"print": {"gcode_state": raw}}) == {
        "print_stats": {"state": normalized}
    }


def test_project_request_preserves_external_capture_hint() -> None:
    assert make_client()._normalize_project_request(
        {
            "print": {
                "command": "project_file",
                "url": "ftps://TEST-SERIAL/cache/benchy.3mf",
                "gcode_state": "RUNNING",
                "subtask_name": "Benchy",
                "task_id": "task-42",
            }
        }
    ) == {
        "print_stats": {
            "state": "printing",
            "filename": "Benchy",
            "external_display_name": "Benchy",
            "external_task_id": "task-42",
            "external_artifact_path": "ftps://TEST-SERIAL/cache/benchy.3mf",
        }
    }


@pytest.mark.asyncio
async def test_query_status_and_snapshot_share_one_lossless_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = {
        "print": {
            "gcode_state": "RUNNING",
            "mc_percent": 25,
            "gcode_file": "/cache/cube.gcode",
            "task_id": "task-42",
        }
    }
    client = make_client(sequence_id_factory=lambda: "query-id")

    def request(
        payload: dict[str, Any], *, accepts: Any, timeout: float = 10.0
    ) -> dict[str, Any]:
        del timeout
        assert payload == {
            "pushing": {
                "sequence_id": "query-id",
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        assert accepts(report) is True
        return report

    monkeypatch.setattr(client, "_mqtt_request", request)
    expected = {
        "result": {
            "status": {
                "print_stats": {
                    "state": "printing",
                    "filename": "cube.gcode",
                    "external_task_id": "task-42",
                    "external_gcode_file": "/cache/cube.gcode",
                },
                "virtual_sdcard": {"progress": 0.25},
            }
        }
    }

    assert await client.query_status() == expected
    snapshot = await client.query_snapshot()
    assert snapshot.state == "printing"
    assert snapshot.filename == "cube.gcode"
    assert snapshot.progress == 0.25
    assert snapshot.print.external_task_id == "task-42"
    assert snapshot.to_legacy_payload() == expected


@pytest.mark.asyncio
async def test_snapshot_subscription_adapts_direct_legacy_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()
    status = {
        "print_stats": {"state": "paused", "filename": "cube.gcode"},
        "virtual_sdcard": {"progress": 0.5},
    }

    async def subscribe(
        callback: Any, *, stop_event: asyncio.Event | None = None
    ) -> None:
        assert stop_event is None
        await callback(status)

    monkeypatch.setattr(client, "subscribe_status", subscribe)
    received = []

    async def on_snapshot(snapshot: Any) -> None:
        received.append(snapshot)

    await client.subscribe_snapshots(on_snapshot)

    assert len(received) == 1
    assert received[0].state == "paused"
    assert received[0].to_legacy_payload() == status


@pytest.mark.asyncio
async def test_unsupported_operations_fail_before_transport() -> None:
    client = make_client()

    for operation, arguments in [
        (client.list_files, ()),
        (client.delete_file, ("cube.gcode",)),
        (client.run_gcode, ("G28",)),
        (client.emergency_stop, ()),
        (client.server_info, ()),
        (client.server_config, ()),
        (client.printer_config, ()),
    ]:
        with pytest.raises(ProviderError) as error:
            await operation(*arguments)
        assert error.value.code == "operation_not_supported_for_provider"


def test_client_satisfies_neutral_runtime_protocols() -> None:
    client = make_client()

    assert isinstance(client, PrinterClient)
    assert isinstance(client, ArtifactCaptureClient)


def test_mqtt_request_preserves_publish_and_cleanup_order() -> None:
    class WireClient(FakeMqttClient):
        def __init__(self) -> None:
            super().__init__()
            self.on_connect: Any = None
            self.on_message: Any = None
            self.calls: list[tuple[Any, ...]] = []

        def connect(self, host: str, port: int, *, keepalive: int) -> None:
            self.calls.append(("connect", host, port, keepalive))
            self.on_connect(self, None, None, 0)

        def subscribe(self, topic: str, *, qos: int) -> None:
            self.calls.append(("subscribe", topic, qos))

        def loop_start(self) -> None:
            self.calls.append(("loop_start",))

        def publish(
            self, topic: str, payload: str, *, qos: int, retain: bool
        ) -> SimpleNamespace:
            self.calls.append(("publish", topic, json.loads(payload), qos, retain))
            response = {"print": {"command": "pause", "result": "success"}}
            message = SimpleNamespace(payload=json.dumps(response).encode())
            self.on_message(self, None, message)
            return SimpleNamespace(rc=0)

        def disconnect(self) -> None:
            self.calls.append(("disconnect",))

        def loop_stop(self) -> None:
            self.calls.append(("loop_stop",))

    wire = WireClient()
    client = make_client(mqtt_client_factory=lambda: wire)
    payload = {"print": {"command": "pause"}}

    assert client._mqtt_request(
        payload, accepts=lambda body: body["print"]["command"] == "pause"
    ) == {"print": {"command": "pause", "result": "success"}}
    assert wire.calls == [
        ("connect", "192.0.2.10", 8883, 30),
        ("subscribe", "device/TEST-SERIAL/report", 1),
        ("loop_start",),
        ("publish", "device/TEST-SERIAL/request", payload, 1, False),
        ("disconnect",),
        ("loop_stop",),
    ]


def test_factory_builds_through_registry_and_injects_all_seams() -> None:
    def mqtt_factory() -> object:
        return object()

    def ftps_factory() -> Any:
        return object()

    def sequence_factory() -> str:
        return "fixed-sequence"

    logger = logging.getLogger("test.bambu.factory")
    factory = BambuFactory(
        mqtt_client_factory=mqtt_factory,
        ftps_client_factory=ftps_factory,
        sequence_id_factory=sequence_factory,
        logger=logger,
    )
    registry = ProviderRegistry([factory])
    config = BambuConfig("192.0.2.10", "TEST-SERIAL", "test-code")

    client = registry.build(ProviderId.BAMBU_LAN, config)

    assert isinstance(client, BambuClient)
    assert client.config is config
    assert client.capabilities is BambuClient.capabilities
    assert factory.capabilities is BambuClient.capabilities
    assert client._mqtt_client_factory is mqtt_factory
    assert client._ftps_client_factory is ftps_factory
    assert client._sequence_id_factory is sequence_factory
    assert client._logger is logger


def test_factory_rejects_another_provider_config() -> None:
    factory = BambuFactory()

    with pytest.raises(ProviderError) as error:
        factory.build(OctoPrintConfig("http://octoprint.local", "key"))

    assert error.value.detail == "provider_config_mismatch"
    assert error.value.code == "provider_config_mismatch"
