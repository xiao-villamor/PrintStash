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
