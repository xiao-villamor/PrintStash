"""Protects observable core behavior for printers / bambu.

Its cases cover command response correlation and rejection codes and the related
outcomes in this module.
"""

from __future__ import annotations

from ._bambu_shared import (
    Any,
    ArtifactCaptureClient,
    BambuClient,
    BambuConfig,
    BambuFactory,
    FakeMqttClient,
    OctoPrintConfig,
    PrinterClient,
    ProviderError,
    ProviderId,
    ProviderRegistry,
    SimpleNamespace,
    asyncio,
    json,
    logging,
    make_client,
    pytest,
)


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
