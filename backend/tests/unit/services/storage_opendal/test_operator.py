"""Remote operator construction maps catalogue transports safely.

The adapter must select OpenDAL WebDAV or pinned-host AsyncSSH SFTP from
typed transport options and surface unavailable remote support clearly.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from app.services import storage_opendal
from app.services.storage_backend import StorageConfigurationError
from app.services.storage_providers import TransportKind, TransportSpec


def _spec(
    kind: TransportKind = TransportKind.WEBDAV,
    *,
    options: dict[str, str | int | bool] | None = None,
) -> TransportSpec:
    return TransportSpec(
        kind=kind,
        provider="test-remote",
        namespace="vault/data",
        options=options or {},
    )


class TestOperatorFor:
    def test_reports_a_missing_opendal_dependency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "opendal", None)

        with pytest.raises(StorageConfigurationError, match="full image"):
            storage_opendal._operator_for(_spec())

    def test_builds_a_webdav_operator_from_catalogue_options(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, dict[str, str]]] = []
        fake_opendal = ModuleType("opendal")

        def build(kind: str, **options: str) -> object:
            calls.append((kind, options))
            return object()

        fake_opendal.Operator = build  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)

        result = storage_opendal._operator_for(
            _spec(
                options={
                    "endpoint_url": "https://dav.example",
                    "root": "vault",
                    "username": "user",
                    "password": "secret",
                }
            )
        )

        assert result is not None
        assert calls == [
            (
                "webdav",
                {
                    "endpoint": "https://dav.example",
                    "root": "vault",
                    "username": "user",
                    "password": "secret",
                },
            )
        ]

    @pytest.mark.parametrize(
        ("addressing_style", "expected_virtual"),
        [("auto", None), ("path", "false"), ("virtual", "true")],
    )
    def test_builds_an_s3_operator_without_overriding_auto_addressing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        addressing_style: str,
        expected_virtual: str | None,
    ) -> None:
        calls: list[tuple[str, dict[str, str]]] = []
        fake_opendal = ModuleType("opendal")

        def build(kind: str, **options: str) -> object:
            calls.append((kind, options))
            return object()

        fake_opendal.Operator = build  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)

        storage_opendal._operator_for(
            _spec(
                TransportKind.S3,
                options={
                    "bucket": "models",
                    "root": "library",
                    "region": "us-east-1",
                    "endpoint_url": "",
                    "addressing_style": addressing_style,
                    "access_key": "access",
                    "secret_key": "secret",
                },
            )
        )

        assert calls[0][0] == "s3"
        options = calls[0][1]
        assert options["disable_config_load"] == "true"
        assert options["disable_ec2_metadata"] == "true"
        assert options["region"] == "us-east-1"
        if expected_virtual is None:
            assert "enable_virtual_host_style" not in options
        else:
            assert options["enable_virtual_host_style"] == expected_virtual

    def test_s3_leaves_region_discovery_to_opendal_when_configured_as_auto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, str]] = []
        fake_opendal = ModuleType("opendal")
        fake_opendal.Operator = (  # type: ignore[attr-defined]
            lambda _kind, **options: calls.append(options) or object()
        )
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)

        storage_opendal._operator_for(
            _spec(
                TransportKind.S3,
                options={
                    "bucket": "models",
                    "root": "library",
                    "region": "auto",
                    "endpoint_url": "",
                    "addressing_style": "auto",
                    "access_key": "access",
                    "secret_key": "secret",
                },
            )
        )

        assert "region" not in calls[0]

    def test_builds_a_google_drive_operator_from_oauth_secrets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, dict[str, str]]] = []
        fake_opendal = ModuleType("opendal")

        def build(kind: str, **options: str) -> object:
            calls.append((kind, options))
            return object()

        fake_opendal.Operator = build  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)

        storage_opendal._operator_for(
            _spec(
                TransportKind.GDRIVE,
                options={
                    "root": "PrintStash",
                    "client_id": "client",
                    "client_secret": "secret",
                    "refresh_token": "refresh",
                },
            )
        )

        assert calls == [
            (
                "gdrive",
                {
                    "root": "PrintStash",
                    "client_id": "client",
                    "client_secret": "secret",
                    "refresh_token": "refresh",
                },
            )
        ]

    def test_reports_an_unregistered_google_drive_transport(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Unsupported(Exception):
            pass

        fake_opendal = ModuleType("opendal")

        def unregistered(_kind: str, **_options: str) -> object:
            raise Unsupported("scheme is not registered")

        fake_opendal.Operator = unregistered  # type: ignore[attr-defined]
        fake_opendal.exceptions = ModuleType("opendal.exceptions")  # type: ignore[attr-defined]
        fake_opendal.exceptions.Unsupported = Unsupported  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)

        with pytest.raises(
            StorageConfigurationError, match="gdrive_transport_unavailable"
        ):
            storage_opendal._operator_for(
                _spec(
                    TransportKind.GDRIVE,
                    options={
                        "root": "PrintStash",
                        "client_id": "client",
                        "client_secret": "secret",
                        "refresh_token": "refresh",
                    },
                )
            )

    def test_builds_a_key_based_sftp_operator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, dict[str, str]]] = []
        fake_opendal = ModuleType("opendal")

        def build(kind: str, **options: str) -> object:
            calls.append((kind, options))
            return object()

        fake_opendal.Operator = build  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)

        result = storage_opendal._operator_for(
            _spec(
                TransportKind.SFTP,
                options={
                    "host": "sftp.example",
                    "port": 22,
                    "username": "user",
                    "root": "vault",
                    "private_key_path": "/tmp/key",
                    "host_key": "ssh-ed25519 AAAA",
                },
            )
        )

        assert isinstance(result, storage_opendal._AsyncSSHSFTPOperator)

    def test_uses_asyncssh_for_password_sftp_auth(self) -> None:
        result = storage_opendal._operator_for(
            _spec(
                TransportKind.SFTP,
                options={
                    "host": "sftp.example",
                    "port": 22,
                    "username": "user",
                    "root": "vault",
                    "password": "secret",
                    "host_key": "ssh-ed25519 AAAA",
                },
            )
        )

        assert isinstance(result, storage_opendal._AsyncSSHSFTPOperator)

    def test_uses_asyncssh_when_opendal_sftp_is_not_registered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_opendal = ModuleType("opendal")

        def unregistered(_kind: str, **_options: str) -> object:
            raise RuntimeError("scheme is not registered")

        fake_opendal.Operator = unregistered  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)

        result = storage_opendal._operator_for(
            _spec(
                TransportKind.SFTP,
                options={
                    "host": "sftp.example",
                    "port": 22,
                    "username": "user",
                    "root": "vault",
                    "host_key": "ssh-ed25519 AAAA",
                },
            )
        )

        assert isinstance(result, storage_opendal._AsyncSSHSFTPOperator)

    def test_reports_an_unavailable_sftp_transport(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_opendal = ModuleType("opendal")

        def broken(_kind: str, **_options: str) -> object:
            raise RuntimeError("unexpected failure")

        fake_opendal.Operator = broken  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)

        with pytest.raises(StorageConfigurationError, match="host_key_required"):
            storage_opendal._operator_for(
                _spec(
                    TransportKind.SFTP,
                    options={
                        "host": "sftp.example",
                        "port": 22,
                        "username": "user",
                        "root": "vault",
                        "host_key": "",
                    },
                )
            )

    def test_rejects_an_unknown_transport(self) -> None:
        with pytest.raises(StorageConfigurationError, match="unsupported remote"):
            storage_opendal._operator_for(_spec(TransportKind.LOCAL))
