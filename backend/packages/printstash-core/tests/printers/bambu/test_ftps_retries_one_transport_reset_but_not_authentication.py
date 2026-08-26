"""Protects observable core behavior for printers / bambu.

Its cases cover ftps retries one transport reset but not authentication and the
related outcomes in this module.
"""

from __future__ import annotations

from ._bambu_shared import (
    _BAMBU_CA_CERTIFICATES,
    Any,
    BambuClient,
    FailingFtpsClient,
    FakeFtpsClient,
    FakeMqttClient,
    Path,
    ProviderError,
    _ImplicitFTP_TLS,
    bambu_module,
    error_perm,
    error_reply,
    hashlib,
    make_client,
    pytest,
    ssl,
)


def test_ftps_retries_one_transport_reset_but_not_authentication(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cube.gcode"
    source.write_bytes(b"G28\n")
    first = FailingFtpsClient(ConnectionResetError("peer reset"))
    second = FakeFtpsClient()
    clients = iter((first, second))
    client = make_client(ftps_client_factory=lambda: next(clients))

    client._upload_via_ftps(source, "cube.gcode")
    assert len([call for call in first.calls if call[0] == "connect"]) == 1
    assert len([call for call in second.calls if call[0] == "connect"]) == 1

    auth = FailingFtpsClient(error_perm("530 Login incorrect"))
    auth_client = make_client(ftps_client_factory=lambda: auth)
    with pytest.raises(ProviderError) as failure:
        auth_client._upload_via_ftps(source, "cube.gcode")
    assert failure.value.code == "provider_transport_error"
    assert failure.value.action_code == "bambu_ftps_authentication_failed"
    assert len([call for call in auth.calls if call[0] == "connect"]) == 1
    wrapped_auth = BambuClient._classify_ftps_exception(
        PermissionError("  530 Login incorrect  ")
    )
    assert wrapped_auth.code == "provider_transport_error"
    assert wrapped_auth.action_code == "bambu_ftps_authentication_failed"
    local_auth_path = BambuClient._classify_ftps_exception(
        PermissionError(13, "Permission denied", "/srv/auth/cache.gcode")
    )
    assert local_auth_path.action_code == "bambu_ftps_local_error"
    assert (
        BambuClient._classify_ftps_exception(ConnectionResetError()).retryable is True
    )


def test_ftps_retries_eof_once(tmp_path: Path) -> None:
    source = tmp_path / "cube.gcode"
    source.write_bytes(b"G28\n")
    first = FailingFtpsClient(EOFError("unexpected EOF"))
    second = FakeFtpsClient()
    clients = iter((first, second))
    client = make_client(ftps_client_factory=lambda: next(clients))

    client._upload_via_ftps(source, "cube.gcode")
    assert len([call for call in first.calls if call[0] == "connect"]) == 1
    assert len([call for call in second.calls if call[0] == "connect"]) == 1
    eof = BambuClient._classify_ftps_exception(EOFError())
    assert eof.action_code == "bambu_ftps_eof"
    assert eof.retryable is True


def test_ftps_classification_exposes_actionable_codes() -> None:
    assert (
        BambuClient._classify_ftps_exception(
            error_perm("550 file unavailable")
        ).action_code
        == "bambu_ftps_not_found"
    )
    assert (
        BambuClient._classify_ftps_exception(
            error_perm("552 storage exceeded")
        ).action_code
        == "bambu_ftps_too_large"
    )
    assert (
        BambuClient._classify_ftps_exception(
            error_perm("450 file unavailable")
        ).action_code
        == "bambu_ftps_not_found"
    )
    assert BambuClient._classify_ftps_exception(TimeoutError()).action_code == (
        "bambu_ftps_timeout"
    )
    assert (
        BambuClient._classify_ftps_exception(
            error_reply("501 command syntax error")
        ).action_code
        == "bambu_ftps_server_rejected"
    )
    assert (
        BambuClient._classify_ftps_exception(
            error_reply("452 insufficient storage")
        ).action_code
        == "bambu_ftps_server_rejected"
    )


def test_provider_error_defaults_action_code_to_safe_coarse_code() -> None:
    error = ProviderError("remote server detail", code="provider_timeout")
    assert error.detail == "remote server detail"
    assert error.action_code == "provider_timeout"
    assert error.retryable is False


def test_ftps_retry_requires_explicit_transport_or_provider_opt_in() -> None:
    for local_exception in (
        FileNotFoundError("local source is missing"),
        PermissionError("local destination is not writable"),
    ):
        local_attempts = 0

        def local_failure(exc: BaseException = local_exception) -> None:
            nonlocal local_attempts
            local_attempts += 1
            raise exc

        with pytest.raises(ProviderError) as local_error:
            BambuClient._with_ftps_retry(local_failure)
        assert local_error.value.action_code == "bambu_ftps_local_error"
        assert local_attempts == 1

    unknown_attempts = 0

    def unknown_failure() -> None:
        nonlocal unknown_attempts
        unknown_attempts += 1
        raise RuntimeError("unexpected local callback failure")

    with pytest.raises(ProviderError) as unknown_error:
        BambuClient._with_ftps_retry(unknown_failure)
    assert unknown_error.value.action_code == "bambu_ftps_unknown_error"
    assert unknown_attempts == 1

    provider_attempts = 0

    def explicitly_retryable_provider_failure() -> None:
        nonlocal provider_attempts
        provider_attempts += 1
        if provider_attempts == 1:
            raise ProviderError("temporary provider outcome", retryable=True)

    with pytest.raises(ProviderError) as generic_error:
        BambuClient._with_ftps_retry(explicitly_retryable_provider_failure)
    assert generic_error.value.action_code == "provider_error"
    assert provider_attempts == 1

    explicit_ftps_attempts = 0

    def explicitly_retryable_ftps_failure() -> None:
        nonlocal explicit_ftps_attempts
        explicit_ftps_attempts += 1
        if explicit_ftps_attempts == 1:
            raise ProviderError(
                "temporary FTPS transport outcome",
                action_code="bambu_ftps_transport_error",
                retryable=True,
            )

    BambuClient._with_ftps_retry(explicitly_retryable_ftps_failure)
    assert explicit_ftps_attempts == 2


def test_missing_local_upload_is_not_retried(tmp_path: Path) -> None:
    factory_attempts = 0

    def client_factory() -> FakeFtpsClient:
        nonlocal factory_attempts
        factory_attempts += 1
        return FakeFtpsClient()

    client = make_client(ftps_client_factory=client_factory)
    with pytest.raises(ProviderError) as missing:
        client._upload_via_ftps(tmp_path / "missing.gcode", "cube.gcode")
    assert missing.value.action_code == "bambu_ftps_local_error"
    assert factory_attempts == 1


def test_ftps_non_retryable_codes_and_path_guards(tmp_path: Path) -> None:
    source = tmp_path / "cube.gcode"
    source.write_bytes(b"G28\n")
    calls = 0

    def not_found_factory() -> FailingFtpsClient:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("non-retryable FTPS error was retried")
        return FailingFtpsClient(error_perm("450 file unavailable"))

    client = make_client(ftps_client_factory=not_found_factory)
    with pytest.raises(ProviderError) as not_found:
        client._download_via_ftps("benchy.3mf", tmp_path / "benchy.3mf", max_bytes=4)
    assert not_found.value.action_code == "bambu_ftps_not_found"
    assert calls == 1

    too_large = make_client(ftps_client_factory=lambda: FakeFtpsClient(remote_size=99))
    with pytest.raises(ProviderError) as size_limit:
        too_large._download_via_ftps(
            "benchy.3mf", tmp_path / "too-large.3mf", max_bytes=4
        )
    assert size_limit.value.action_code == "bambu_ftps_too_large"

    response_calls = 0

    def too_large_response_factory() -> FailingFtpsClient:
        nonlocal response_calls
        response_calls += 1
        if response_calls > 1:
            raise AssertionError("552 FTPS error was retried")
        return FailingFtpsClient(error_perm("552 storage exceeded"))

    response_limit = make_client(ftps_client_factory=too_large_response_factory)
    with pytest.raises(ProviderError) as response_too_large:
        response_limit._download_via_ftps(
            "benchy.3mf", tmp_path / "response-too-large.3mf", max_bytes=4
        )
    assert response_too_large.value.action_code == "bambu_ftps_too_large"
    assert response_calls == 1

    mismatch = make_client(ftps_client_factory=lambda: FakeFtpsClient(remote_size=99))
    with pytest.raises(ProviderError) as size_mismatch:
        mismatch._upload_via_ftps(source, "cube.gcode")
    assert size_mismatch.value.action_code == "bambu_ftps_size_mismatch"

    invalid = make_client()
    with pytest.raises(ProviderError) as invalid_path:
        invalid._download_via_ftps(
            "cache/../benchy.3mf", tmp_path / "invalid", max_bytes=4
        )
    assert invalid_path.value.action_code == "bambu_ftps_path_invalid"

    unknown_calls = 0

    def unknown_reply_factory() -> FailingFtpsClient:
        nonlocal unknown_calls
        unknown_calls += 1
        return FailingFtpsClient(error_reply("501 command syntax error"))

    unknown = make_client(ftps_client_factory=unknown_reply_factory)
    with pytest.raises(ProviderError) as unknown_error:
        unknown._upload_via_ftps(source, "cube.gcode")
    assert unknown_error.value.action_code == "bambu_ftps_server_rejected"
    assert unknown_calls == 1


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
