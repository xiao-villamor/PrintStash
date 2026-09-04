"""Remote transport dependencies stay explicit.

These checks make missing optional dependencies visible to provider selection,
with OpenDAL owning WebDAV and AsyncSSH owning every SFTP setup.
"""

from __future__ import annotations

import sys

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
    def test_reports_sftp_available_without_opendal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(storage_opendal, "opendal_available", lambda: False)

        assert storage_opendal.opendal_transport_available(TransportKind.SFTP) is True

    def test_reports_webdav_available_without_probing_sftp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(storage_opendal, "opendal_available", lambda: True)

        assert storage_opendal.opendal_transport_available(TransportKind.WEBDAV) is True

    def test_reports_sftp_unavailable_when_asyncssh_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "asyncssh", None)

        assert storage_opendal.opendal_transport_available(TransportKind.SFTP) is False
