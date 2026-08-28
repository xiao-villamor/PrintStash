"""OpenDAL dependency and transport availability stay explicit.

These checks make missing optional dependencies visible to provider selection,
including the AsyncSSH fallback used by password and passphrase SFTP setups.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from app.services import storage_opendal
from app.services.storage_providers import TransportKind


class TestOpendalAvailable:
    def test_reports_true_when_the_dependency_is_installed(self) -> None:
        assert storage_opendal.opendal_available() is True

    def test_reports_false_when_the_dependency_cannot_be_imported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "opendal", None)

        assert storage_opendal.opendal_available() is False


class TestOpendalTransportAvailable:
    def test_reports_false_when_opendal_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(storage_opendal, "opendal_available", lambda: False)

        assert storage_opendal.opendal_transport_available(TransportKind.SFTP) is False

    def test_reports_webdav_available_without_probing_sftp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(storage_opendal, "opendal_available", lambda: True)

        assert storage_opendal.opendal_transport_available(TransportKind.WEBDAV) is True

    def test_reports_sftp_available_when_the_opendal_service_is_registered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_opendal = ModuleType("opendal")
        fake_opendal.Operator = lambda _kind: object()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)
        monkeypatch.setattr(storage_opendal, "opendal_available", lambda: True)

        assert storage_opendal.opendal_transport_available(TransportKind.SFTP) is True

    def test_reports_sftp_available_when_asyncssh_provides_the_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_opendal = ModuleType("opendal")

        def unregistered(_kind: str) -> object:
            raise RuntimeError("scheme is not registered")

        fake_opendal.Operator = unregistered  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)
        monkeypatch.setattr(storage_opendal, "opendal_available", lambda: True)

        assert storage_opendal.opendal_transport_available(TransportKind.SFTP) is True

    def test_reports_sftp_unavailable_when_the_fallback_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_opendal = ModuleType("opendal")

        def unregistered(_kind: str) -> object:
            raise RuntimeError("scheme is not registered")

        fake_opendal.Operator = unregistered  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)
        monkeypatch.setitem(sys.modules, "asyncssh", None)
        monkeypatch.setattr(storage_opendal, "opendal_available", lambda: True)

        assert storage_opendal.opendal_transport_available(TransportKind.SFTP) is False

    def test_reports_sftp_available_for_an_unexpected_probe_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_opendal = ModuleType("opendal")

        def broken(_kind: str) -> object:
            raise RuntimeError("connection refused")

        fake_opendal.Operator = broken  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "opendal", fake_opendal)
        monkeypatch.setattr(storage_opendal, "opendal_available", lambda: True)

        assert storage_opendal.opendal_transport_available(TransportKind.SFTP) is True
